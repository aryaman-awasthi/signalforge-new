"""Onboarding repositories (REQ-036, REQ-013).

Tenant isolation lives here, not in the handlers: every read and write carries
the tenant_id predicate inside the class, and a cross-tenant get returns None so
the service above answers the same 404 it would for an id that exists nowhere.

There is no cross-tenant method in this module at all. Nothing about onboarding
needs one - unlike the connector health loop, no background job walks every
tenant's applications.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.contracts.canonical import TopologySnapshot
from signalforge.domain.onboarding.applications import (
    Application,
    AppTier,
    Environment,
)
from signalforge.domain.onboarding.codec import profile_from_sections, profile_to_sections
from signalforge.domain.onboarding.ports import (
    ApplicationRepository,
    OnboardingProfileRepository,
    TopologyVersion,
    TopologyVersionRepository,
)
from signalforge.domain.onboarding.profile import OnboardingProfile
from signalforge.infrastructure.persistence.models import (
    ApplicationRow,
    OnboardingProfileRow,
    TopologyVersionRow,
)


class SqlApplicationRepository(ApplicationRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, application: Application) -> None:
        self._s.add(
            ApplicationRow(
                id=application.id,
                tenant_id=application.tenant_id,
                app_id=application.app_id,
                name=application.name,
                tier=application.tier.value,
                environment=application.environment.value,
                base_url=application.base_url,
                description=application.description,
                owner_team=application.owner_team,
                created_at=application.created_at,
                created_by=application.created_by,
                updated_at=application.updated_at or application.created_at,
            )
        )
        await self._s.flush()

    async def get(self, tenant_id: str, app_id: str) -> Optional[Application]:
        row = (
            await self._s.execute(
                select(ApplicationRow).where(
                    ApplicationRow.tenant_id == tenant_id,
                    ApplicationRow.app_id == app_id,
                )
            )
        ).scalars().first()
        return _to_application(row)

    async def list(self, tenant_id: str) -> Sequence[Application]:
        rows = (
            await self._s.execute(
                select(ApplicationRow)
                .where(ApplicationRow.tenant_id == tenant_id)
                .order_by(ApplicationRow.app_id)
            )
        ).scalars().all()
        return [_to_application(row) for row in rows]

    async def update(self, application: Application) -> None:
        await self._s.execute(
            update(ApplicationRow)
            .where(
                ApplicationRow.tenant_id == application.tenant_id,
                ApplicationRow.app_id == application.app_id,
            )
            .values(
                name=application.name,
                tier=application.tier.value,
                environment=application.environment.value,
                base_url=application.base_url,
                description=application.description,
                owner_team=application.owner_team,
                updated_at=application.updated_at,
            )
        )
        await self._s.flush()

    async def delete(self, tenant_id: str, app_id: str) -> bool:
        result = await self._s.execute(
            delete(ApplicationRow).where(
                ApplicationRow.tenant_id == tenant_id,
                ApplicationRow.app_id == app_id,
            )
        )
        return bool(result.rowcount)


class SqlOnboardingProfileRepository(OnboardingProfileRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def get(self, tenant_id: str, app_id: str) -> Optional[OnboardingProfile]:
        row = (
            await self._s.execute(
                select(OnboardingProfileRow).where(
                    OnboardingProfileRow.tenant_id == tenant_id,
                    OnboardingProfileRow.app_id == app_id,
                )
            )
        ).scalars().first()
        if row is None:
            return None
        return profile_from_sections(
            row.tenant_id,
            row.app_id,
            {
                "signals": row.signals,
                "region_label": row.region_label,
                "regions": row.regions,
                "journeys": row.journeys,
                "probes": row.probes,
                "actions": row.actions,
                "slos": row.slos,
                "severity_matrix": row.severity_matrix,
                "policy": row.policy,
                "baselines": row.baselines,
                "topology": row.topology,
                "last_test": row.last_test,
            },
            updated_at=row.updated_at,
        )

    async def put(self, profile: OnboardingProfile) -> None:
        sections = profile_to_sections(profile)
        values = {
            "signals": sections["signals"],
            "region_label": sections["region_label"],
            "regions": sections["regions"],
            "journeys": sections["journeys"],
            "probes": sections["probes"],
            "actions": sections["actions"],
            "slos": sections["slos"],
            "severity_matrix": sections["severity_matrix"],
            "policy": sections["policy"],
            "baselines": sections["baselines"],
            "topology": sections["topology"],
            "last_test": sections["last_test"],
            "updated_at": profile.updated_at,
        }
        result = await self._s.execute(
            update(OnboardingProfileRow)
            .where(
                OnboardingProfileRow.tenant_id == profile.tenant_id,
                OnboardingProfileRow.app_id == profile.app_id,
            )
            .values(**values)
        )
        if not result.rowcount:
            self._s.add(
                OnboardingProfileRow(
                    tenant_id=profile.tenant_id, app_id=profile.app_id, **values
                )
            )
        await self._s.flush()

    async def delete(self, tenant_id: str, app_id: str) -> bool:
        result = await self._s.execute(
            delete(OnboardingProfileRow).where(
                OnboardingProfileRow.tenant_id == tenant_id,
                OnboardingProfileRow.app_id == app_id,
            )
        )
        return bool(result.rowcount)


class SqlTopologyVersionRepository(TopologyVersionRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, version: TopologyVersion) -> None:
        self._s.add(
            TopologyVersionRow(
                tenant_id=version.tenant_id,
                app_id=version.app_id,
                version=version.version,
                snapshot=version.snapshot.model_dump(mode="json"),
                node_count=version.node_count,
                edge_count=version.edge_count,
                imported_at=version.imported_at,
                imported_by=version.imported_by,
                source=version.source,
                normalized_by=version.normalized_by,
            )
        )
        await self._s.flush()

    async def find(
        self, tenant_id: str, app_id: str, version: str
    ) -> Optional[TopologyVersion]:
        row = (
            await self._s.execute(
                select(TopologyVersionRow).where(
                    TopologyVersionRow.tenant_id == tenant_id,
                    TopologyVersionRow.app_id == app_id,
                    TopologyVersionRow.version == version,
                )
            )
        ).scalars().first()
        return _to_version(row)

    async def latest(self, tenant_id: str, app_id: str) -> Optional[TopologyVersion]:
        row = (
            await self._s.execute(
                select(TopologyVersionRow)
                .where(
                    TopologyVersionRow.tenant_id == tenant_id,
                    TopologyVersionRow.app_id == app_id,
                )
                .order_by(TopologyVersionRow.id.desc())
                .limit(1)
            )
        ).scalars().first()
        return _to_version(row)

    async def list(
        self, tenant_id: str, app_id: str, *, limit: int = 50
    ) -> Sequence[TopologyVersion]:
        rows = (
            await self._s.execute(
                select(TopologyVersionRow)
                .where(
                    TopologyVersionRow.tenant_id == tenant_id,
                    TopologyVersionRow.app_id == app_id,
                )
                .order_by(TopologyVersionRow.id.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars().all()
        return [_to_version(row) for row in rows]

    async def delete_all(self, tenant_id: str, app_id: str) -> int:
        result = await self._s.execute(
            delete(TopologyVersionRow).where(
                TopologyVersionRow.tenant_id == tenant_id,
                TopologyVersionRow.app_id == app_id,
            )
        )
        return int(result.rowcount or 0)


def _to_application(row: Optional[ApplicationRow]) -> Optional[Application]:
    if row is None:
        return None
    return Application(
        id=row.id,
        tenant_id=row.tenant_id,
        app_id=row.app_id,
        name=row.name,
        tier=AppTier(row.tier),
        environment=Environment(row.environment),
        base_url=row.base_url,
        description=row.description or "",
        owner_team=row.owner_team or "",
        created_at=row.created_at,
        created_by=row.created_by,
        updated_at=row.updated_at,
    )


def _to_version(row: Optional[TopologyVersionRow]) -> Optional[TopologyVersion]:
    if row is None:
        return None
    return TopologyVersion(
        tenant_id=row.tenant_id,
        app_id=row.app_id,
        version=row.version,
        snapshot=TopologySnapshot.model_validate(row.snapshot),
        imported_at=row.imported_at,
        imported_by=row.imported_by or "",
        source=row.source or "upload",
        normalized_by=row.normalized_by or "",
    )
