"""Ordering lanes.

The bus guarantees order per (consumer group, partition key). It gets there by
mapping every partition key onto one of a fixed number of lanes and giving each
(group, lane) pair its own stored offset, so a slow or dead-lettered partition
never blocks an unrelated one.

The mapping must be a pure, stable function of the key: Python's built-in hash()
is salted per process, so two workers - or the same worker after a restart -
would disagree about which lane an event belongs to and ordering would break
silently. blake2b gives the same answer everywhere, forever.
"""

from __future__ import annotations

import hashlib

LANE_COUNT = 16
"""Fixed at write time and stored on the event row; changing it re-shards the
bus and requires a migration that recomputes every lane."""


def lane_for(partition_key: str, lane_count: int = LANE_COUNT) -> int:
    if lane_count < 1:
        raise ValueError("lane_count must be at least 1")
    digest = hashlib.blake2b(partition_key.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % lane_count
