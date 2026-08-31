"""The adapter registry: manifests discovered from the filesystem (REQ-030).

Discovery is `<root>/**/manifest.yaml`, so an adapter is a folder and installing
one is copying a directory. Nothing here knows any adapter by name.

Two failure rules matter more than the loading itself:

- A manifest that will not parse or will not validate is *recorded*, not raised.
  One broken folder must not take the registry - and therefore the platform's
  whole integration surface - down with it. The error is kept with its path and
  surfaced on the registry API so the person who wrote the manifest can see it.
- A duplicate id is refused rather than silently overwritten. Two folders
  claiming the same adapter id is an install mistake, and picking a winner by
  directory-walk order would make the running configuration depend on the
  filesystem.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

import yaml

from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.errors import ManifestValidationError
from signalforge.domain.adapters.errors import AdapterNotFoundError
from signalforge.domain.adapters.manifest import (
    AdapterManifest,
    AdapterMode,
    CredentialScope,
    manifest_from_mapping,
)
from signalforge.domain.adapters.matrix import CapabilityMatrix, capability_matrix
from signalforge.domain.adapters.ports import AdapterRegistry, ManifestLoadError

logger = logging.getLogger("signalforge.adapters")

MANIFEST_FILENAME = "manifest.yaml"


class FileAdapterRegistry(AdapterRegistry):
    """Loads once at composition time; `reload()` re-reads the tree."""

    def __init__(self, root: Path):
        self._root = Path(root)
        self._manifests: dict[str, AdapterManifest] = {}
        self._errors: list[ManifestLoadError] = []
        self.reload()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def errors(self) -> Sequence[ManifestLoadError]:
        return tuple(self._errors)

    def reload(self) -> None:
        manifests: dict[str, AdapterManifest] = {}
        errors: list[ManifestLoadError] = []
        # A deployment with no adapters installed is empty, not broken: the
        # registry is how adapters arrive, so it has to exist before they do.
        for path in sorted(self._root.rglob(MANIFEST_FILENAME)):
            try:
                manifest = self._load(path)
            except (ManifestValidationError, yaml.YAMLError, OSError, ValueError) as exc:
                errors.append(
                    ManifestLoadError(
                        path=self._relative(path), error=f"{type(exc).__name__}: {exc}"
                    )
                )
                logger.warning(
                    "adapter manifest rejected",
                    extra={"manifest": self._relative(path), "error": str(exc)},
                )
                continue
            if manifest.id in manifests:
                errors.append(
                    ManifestLoadError(
                        path=self._relative(path),
                        error=(
                            f"duplicate adapter id {manifest.id!r}; already declared "
                            "elsewhere in the adapters tree"
                        ),
                    )
                )
                continue
            manifests[manifest.id] = manifest
        self._manifests = manifests
        self._errors = errors

    def _load(self, path: Path) -> AdapterManifest:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if document is None:
            raise ManifestValidationError("manifest is empty")
        return manifest_from_mapping(document)

    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self._root)).replace("\\", "/")
        except ValueError:
            return str(path)

    # -- AdapterRegistry ---------------------------------------------------

    def all(self) -> Sequence[AdapterManifest]:
        return tuple(sorted(self._manifests.values(), key=lambda m: m.id))

    def find(self, adapter_id: str) -> Optional[AdapterManifest]:
        return self._manifests.get(adapter_id)

    def get(self, adapter_id: str) -> AdapterManifest:
        manifest = self._manifests.get(adapter_id)
        if manifest is None:
            raise AdapterNotFoundError(f"adapter {adapter_id!r} is not installed")
        return manifest

    def list(
        self,
        *,
        kind: Optional[AdapterKind] = None,
        mode: Optional[AdapterMode] = None,
        scope: Optional[CredentialScope] = None,
        simulated: Optional[bool] = None,
    ) -> Sequence[AdapterManifest]:
        return tuple(
            manifest
            for manifest in self.all()
            if (kind is None or manifest.kind is kind)
            and (mode is None or manifest.mode is mode)
            and (scope is None or manifest.allows_scope(scope))
            and (simulated is None or manifest.simulated is simulated)
        )

    def matrix(self) -> CapabilityMatrix:
        return capability_matrix(self.all())
