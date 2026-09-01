"""Auth use cases: signup, login, refresh rotation, logout, authenticate,
authorize (REQ-010, REQ-012).

Failure design: login failures are generic and rate limited; refresh-token
reuse revokes the whole session family; every security-relevant outcome lands
in the audit chain.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Optional

from signalforge.contracts.roles import Permission, Role
from signalforge.domain.identity.entities import (
    RoleBinding,
    Session,
    Tenant,
    User,
    normalize_email,
)
from signalforge.domain.identity.errors import (
    AuthenticationError,
    DomainValidationError,
    PermissionDeniedError,
    RateLimitedError,
)
from signalforge.domain.identity.ports import (
    AccessTokenService,
    Clock,
    PasswordHasher,
    RateLimiter,
    SocketTicketService,
)
from signalforge.domain.identity.rbac import Decision, RBACDecisionService
from signalforge.domain.identity.tokens import hash_refresh_token, new_refresh_token
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork

MIN_PASSWORD_LENGTH = 12

UowFactory = Callable[[], IdentityUnitOfWork]


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in_s: int


@dataclass(frozen=True)
class AuthResult:
    user: User
    tenant: Tenant
    bindings: tuple[RoleBinding, ...]
    tokens: TokenPair


@dataclass(frozen=True)
class SocketTicket:
    """What an authenticated session gets to open one socket with."""

    ticket: str
    expires_in_s: int
    url_path: str = "/ws"
    subprotocol: str = "ticket"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise DomainValidationError("organisation name must contain letters or digits")
    return slug[:80]


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise DomainValidationError(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters"
        )


class AuthService:
    def __init__(
        self,
        uow_factory: UowFactory,
        hasher: PasswordHasher,
        tokens: AccessTokenService,
        rate_limiter: RateLimiter,
        clock: Clock,
        *,
        access_ttl_s: int,
        refresh_ttl_s: int,
        socket_tickets: Optional[SocketTicketService] = None,
    ):
        self._uow_factory = uow_factory
        self._hasher = hasher
        self._tokens = tokens
        self._rate_limiter = rate_limiter
        self._clock = clock
        self._access_ttl_s = access_ttl_s
        self._refresh_ttl_s = refresh_ttl_s
        self._socket_tickets = socket_tickets

    # -- signup / login -----------------------------------------------------

    async def signup(
        self, *, org_name: str, email: str, password: str, display_name: str
    ) -> AuthResult:
        email = normalize_email(email)
        _check_password(password)
        now = self._clock.now()
        async with self._uow_factory() as uow:
            slug = base_slug = _slugify(org_name)
            suffix = 2
            while await uow.tenants.get_by_slug(slug) is not None:
                slug = f"{base_slug}-{suffix}"
                suffix += 1
            tenant = Tenant(id=str(uuid.uuid4()), name=org_name, slug=slug, created_at=now)
            await uow.tenants.add(tenant)
            user = User(
                id=str(uuid.uuid4()), tenant_id=tenant.id, email=email,
                display_name=display_name,
                password_hash=self._hasher.hash(password), created_at=now,
            )
            await uow.users.add(user)
            bindings = (
                RoleBinding(tenant_id=tenant.id, user_id=user.id, role=Role.ORG_OWNER),
            )
            await uow.bindings.replace_for_user(tenant.id, user.id, bindings)
            await uow.audit.append(
                tenant_id=tenant.id, topic="audit.auth.signup",
                actor_type="human", actor_id=user.id,
                payload={"email": user.email, "org_slug": slug},
            )
            tokens = await self._start_session(uow, tenant.id, user.id)
            await uow.commit()
        return AuthResult(user=user, tenant=tenant, bindings=bindings, tokens=tokens)

    async def login(self, *, org_slug: str, email: str, password: str) -> AuthResult:
        email_norm = email.strip().lower()
        rate_key = f"login:{org_slug}:{email_norm}"
        try:
            self._rate_limiter.hit(rate_key)
        except RateLimitedError:
            await self._audit_login(org_slug, email_norm, "audit.auth.login_rate_limited")
            raise
        async with self._uow_factory() as uow:
            tenant = await uow.tenants.get_by_slug(org_slug)
            user = (
                await uow.users.get_by_email(tenant.id, email_norm) if tenant else None
            )
            if (
                tenant is None
                or not tenant.is_active
                or user is None
                or not user.is_active
                or not self._hasher.verify(password, user.password_hash)
            ):
                if tenant is not None:
                    await uow.audit.append(
                        tenant_id=tenant.id, topic="audit.auth.login_failed",
                        actor_type="human", actor_id=email_norm,
                        payload={"org_slug": org_slug},
                    )
                    await uow.commit()
                raise AuthenticationError("invalid credentials")
            bindings = tuple(await uow.bindings.list_for_user(tenant.id, user.id))
            await uow.audit.append(
                tenant_id=tenant.id, topic="audit.auth.login_succeeded",
                actor_type="human", actor_id=user.id, payload={"org_slug": org_slug},
            )
            tokens = await self._start_session(uow, tenant.id, user.id)
            await uow.commit()
        self._rate_limiter.reset(rate_key)
        return AuthResult(user=user, tenant=tenant, bindings=bindings, tokens=tokens)

    # -- refresh / logout ---------------------------------------------------

    async def refresh(self, refresh_token: str) -> TokenPair:
        token_hash = hash_refresh_token(refresh_token)
        now = self._clock.now()
        async with self._uow_factory() as uow:
            session = await uow.sessions.get_by_token_hash(token_hash)
            if session is None:
                raise AuthenticationError("invalid credentials")
            if session.refresh_token_hash != token_hash:
                # The previous token of this session was presented again:
                # reuse means theft of one side of the rotation - kill the family.
                await uow.sessions.revoke_family(
                    session.tenant_id, session.family_id,
                    "refresh token reuse detected", now,
                )
                await uow.audit.append(
                    tenant_id=session.tenant_id, topic="audit.auth.refresh_reuse_detected",
                    actor_type="human", actor_id=session.user_id,
                    payload={"session_family": session.family_id},
                )
                await uow.commit()
                raise AuthenticationError("invalid credentials")
            if not session.is_active(now):
                raise AuthenticationError("invalid credentials")
            new_token = new_refresh_token()
            session.prev_token_hash = token_hash
            session.refresh_token_hash = hash_refresh_token(new_token)
            session.rotated_at = now
            await uow.sessions.update(session)
            await uow.commit()
        access = self._tokens.issue(
            user_id=session.user_id, tenant_id=session.tenant_id, session_id=session.id
        )
        return TokenPair(access, new_token, self._access_ttl_s)

    async def logout(self, principal: Principal) -> None:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            session = await uow.sessions.get(principal.tenant_id, principal.session_id)
            if session is not None and session.revoked_at is None:
                session.revoked_at = now
                session.revoked_reason = "logout"
                await uow.sessions.update(session)
            await uow.audit.append(
                tenant_id=principal.tenant_id, topic="audit.auth.logout",
                actor_type="human", actor_id=principal.user_id, payload={},
            )
            await uow.commit()

    # -- request-time verification -----------------------------------------

    async def authenticate(self, access_token: str) -> Principal:
        return await self._principal_for(self._tokens.verify(access_token))

    async def _principal_for(self, claims: dict) -> Principal:
        """Signed claims to a live Principal, or nothing.

        Shared by the bearer token and the socket ticket so the two can never
        drift: whichever credential arrived, the session must still exist and be
        active and the user must still exist and be active, and the role
        bindings are read now rather than carried in the credential - a role
        removed a minute ago is removed on the next request.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            session = await uow.sessions.get(claims["tid"], claims["sid"])
            if session is None or not session.is_active(now):
                raise AuthenticationError("invalid or expired token")
            user = await uow.users.get(claims["tid"], claims["sub"])
            if user is None or not user.is_active:
                raise AuthenticationError("invalid or expired token")
            if session.user_id != user.id:  # pragma: no cover - a floor, not a branch
                raise AuthenticationError("invalid or expired token")
            bindings = tuple(await uow.bindings.list_for_user(user.tenant_id, user.id))
        return Principal(
            user_id=user.id, tenant_id=user.tenant_id, session_id=session.id,
            email=user.email, display_name=user.display_name, bindings=bindings,
        )

    # -- the socket credential ---------------------------------------------

    def issue_socket_ticket(self, principal: Principal) -> SocketTicket:
        """One handshake's worth of credential, for the caller's own session.

        There is no parameter: the ticket is cut from the principal the request
        already resolved, so a session cannot ask for a ticket naming somebody
        else's user, session or tenant. That is what lets the route carry no
        permission - the frozen matrix gives the SME none, and the SME is the
        person whose pager this socket feeds.

        Deliberately not audited. A cockpit takes a ticket on every reconnect,
        so auditing issuance would write a row per network blip and drown the
        chain that a post-incident review reads; the acts that matter - the
        login that created the session, and everything done over the socket -
        are audited where they happen.
        """
        if self._socket_tickets is None:  # pragma: no cover - always wired
            raise AuthenticationError("this node issues no socket tickets")
        ticket = self._socket_tickets.issue(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            session_id=principal.session_id,
        )
        return SocketTicket(ticket=ticket, expires_in_s=self._socket_tickets.ttl_s)

    async def authenticate_socket_ticket(self, ticket: str) -> Principal:
        """Redeem a ticket into the same Principal a bearer token would produce.

        The session is re-read and re-checked here rather than trusted from the
        ticket: a ticket cut a moment before a logout, a revocation or a
        deactivation must not outlive it. The tenant and the user come from the
        ticket's own signed claims and are resolved against each other, so a
        ticket cannot select a session in another tenant.
        """
        if self._socket_tickets is None:  # pragma: no cover - always wired
            raise AuthenticationError("this node issues no socket tickets")
        claims = self._socket_tickets.redeem(ticket)
        return await self._principal_for(claims)

    async def authorize(
        self,
        principal: Principal,
        permission: Permission,
        app_id: Optional[str] = None,
        *,
        resource: str = "",
    ) -> Decision:
        decision = RBACDecisionService.decide(principal.bindings, permission, app_id)
        if not decision.allowed:
            async with self._uow_factory() as uow:
                await uow.audit.append(
                    tenant_id=principal.tenant_id, topic="audit.authz.denied",
                    actor_type="human", actor_id=principal.user_id,
                    payload={
                        "permission": permission.value,
                        "resource": resource,
                        "reason": decision.reason,
                    },
                    app_id=app_id,
                )
                await uow.commit()
            raise PermissionDeniedError(decision.reason, decision)
        return decision

    # -- helpers ------------------------------------------------------------

    async def _start_session(
        self, uow: IdentityUnitOfWork, tenant_id: str, user_id: str
    ) -> TokenPair:
        now = self._clock.now()
        refresh = new_refresh_token()
        session = Session(
            id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id,
            family_id=str(uuid.uuid4()),
            refresh_token_hash=hash_refresh_token(refresh),
            issued_at=now, expires_at=now + timedelta(seconds=self._refresh_ttl_s),
        )
        await uow.sessions.add(session)
        access = self._tokens.issue(
            user_id=user_id, tenant_id=tenant_id, session_id=session.id
        )
        return TokenPair(access, refresh, self._access_ttl_s)

    async def _audit_login(self, org_slug: str, email: str, topic: str) -> None:
        async with self._uow_factory() as uow:
            tenant = await uow.tenants.get_by_slug(org_slug)
            if tenant is None:
                return
            await uow.audit.append(
                tenant_id=tenant.id, topic=topic,
                actor_type="human", actor_id=email, payload={"org_slug": org_slug},
            )
            await uow.commit()
