"""Loading and validation for the frozen manifest JSON schemas."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

from signalforge.contracts.errors import ManifestValidationError

_SCHEMA_DIR = Path(__file__).parent / "schemas"

ACTION_MANIFEST_SCHEMA = "action-manifest.schema.json"
CHAOS_MANIFEST_SCHEMA = "chaos-manifest.schema.json"


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _validate(document: dict[str, Any], schema_name: str) -> None:
    schema = load_schema(schema_name)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in errors[:5]
        )
        raise ManifestValidationError(f"{schema_name}: {detail}")


def validate_action_manifest(document: dict[str, Any]) -> None:
    """Raise ManifestValidationError unless the document is a valid action manifest."""
    _validate(document, ACTION_MANIFEST_SCHEMA)


def validate_chaos_manifest(document: dict[str, Any]) -> None:
    """Raise ManifestValidationError unless the document is a valid chaos manifest."""
    _validate(document, CHAOS_MANIFEST_SCHEMA)
