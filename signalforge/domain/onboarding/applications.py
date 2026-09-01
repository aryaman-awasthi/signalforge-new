"""The application being onboarded (REQ-036).

`app_id` is already the tenant-scoped key everything else in the platform uses -
role bindings, connector bindings, ingest keys, events, audit entries - so this
entity does not invent a new identifier. It gives that string a name, an owner,
a tier and, most importantly, a **base URL**.

The base URL is the one piece of the Basics step that does real work later: it is
what the auto-discover probe walks looking for the frozen `/observe/*` contract,
and it is what pre-fills the `base_url` config field of every adapter whose
config schema asks for one. Everything else in Basics is description.

Pure value object: parsing a URL is stdlib, and refusing a non-http(s) scheme is
a control rather than tidiness - the platform later makes requests to whatever is
stored here, so `file://` would turn a text field into a local file read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Optional
from urllib.parse import urlparse

from signalforge.domain.onboarding.errors import OnboardingValidationError

APP_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
APP_NAME_PATTERN = re.compile(r"^[\w][\w .:/&'-]{0,79}$")

ALLOWED_URL_SCHEMES = ("http", "https")
MAX_BASE_URL_CHARS = 300
MAX_DESCRIPTION_CHARS = 1000
MAX_OWNER_CHARS = 120


class AppTier(str, Enum):
    """How much the business cares. Tier drives the severity matrix, not the
    other way round: a tier-1 journey failing is what makes an incident a P0."""

    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"

    @property
    def rank(self) -> int:
        return {"tier_1": 1, "tier_2": 2, "tier_3": 3}[self.value]


class Environment(str, Enum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"


def validate_app_id(app_id: str) -> str:
    text = (app_id or "").strip()
    if not APP_ID_PATTERN.match(text):
        raise OnboardingValidationError(
            "app_id must be lowercase kebab-case, 3-64 characters"
        )
    return text


def normalize_base_url(base_url: Optional[str]) -> Optional[str]:
    """Absolute http(s) only, trailing slash stripped so two spellings of the
    same root are one value. None and empty both mean "not set yet", because the
    Basics step can be saved before anyone knows the URL."""
    if base_url is None:
        return None
    text = base_url.strip()
    if not text:
        return None
    if len(text) > MAX_BASE_URL_CHARS:
        raise OnboardingValidationError(
            f"base_url must be at most {MAX_BASE_URL_CHARS} characters"
        )
    parsed = urlparse(text)
    if parsed.scheme not in ALLOWED_URL_SCHEMES or not parsed.netloc:
        raise OnboardingValidationError(
            f"base_url must be an absolute http(s) URL, got {base_url!r}"
        )
    if parsed.query or parsed.fragment:
        raise OnboardingValidationError(
            "base_url is a root URL; it carries no query string or fragment"
        )
    return text.rstrip("/")


@dataclass(frozen=True)
class Application:
    id: str
    tenant_id: str
    app_id: str
    name: str
    tier: AppTier = AppTier.TIER_2
    environment: Environment = Environment.PRODUCTION
    base_url: Optional[str] = None
    description: str = ""
    owner_team: str = ""
    created_at: Optional[datetime] = None
    created_by: str = ""
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "app_id", validate_app_id(self.app_id))
        if not APP_NAME_PATTERN.match(self.name or ""):
            raise OnboardingValidationError(
                "application name must be 1-80 printable characters"
            )
        if len(self.description or "") > MAX_DESCRIPTION_CHARS:
            raise OnboardingValidationError(
                f"description must be at most {MAX_DESCRIPTION_CHARS} characters"
            )
        if len(self.owner_team or "") > MAX_OWNER_CHARS:
            raise OnboardingValidationError(
                f"owner_team must be at most {MAX_OWNER_CHARS} characters"
            )
        object.__setattr__(self, "base_url", normalize_base_url(self.base_url))

    def edited(
        self,
        *,
        at: datetime,
        name: Optional[str] = None,
        tier: Optional[AppTier] = None,
        environment: Optional[Environment] = None,
        base_url: Optional[str] = None,
        description: Optional[str] = None,
        owner_team: Optional[str] = None,
    ) -> "Application":
        return replace(
            self,
            name=self.name if name is None else name,
            tier=self.tier if tier is None else tier,
            environment=self.environment if environment is None else environment,
            base_url=self.base_url if base_url is None else base_url,
            description=self.description if description is None else description,
            owner_team=self.owner_team if owner_team is None else owner_team,
            updated_at=at,
        )

    def url_for(self, path: str) -> str:
        """An absolute URL under this application's root, for a discovery probe
        or a journey probe. Raises rather than guessing when Basics has no URL:
        a probe against a guessed host is worse than no probe."""
        if not self.base_url:
            raise OnboardingValidationError(
                f"application {self.app_id!r} has no base_url; set it in Basics "
                "before discovering or probing endpoints"
            )
        return f"{self.base_url}/{path.lstrip('/')}"
