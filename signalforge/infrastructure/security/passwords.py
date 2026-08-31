"""Argon2id password hashing (REQ-010)."""

from __future__ import annotations

from argon2 import PasswordHasher as _Argon2Hasher
from argon2 import exceptions as argon2_exceptions
from argon2.low_level import Type

from signalforge.domain.identity.ports import PasswordHasher


class Argon2PasswordHasher(PasswordHasher):
    def __init__(self) -> None:
        # Explicit type=ID: the argon2id variant is the requirement, not a default.
        self._hasher = _Argon2Hasher(type=Type.ID)

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (
            argon2_exceptions.VerifyMismatchError,
            argon2_exceptions.VerificationError,
            argon2_exceptions.InvalidHashError,
        ):
            return False
