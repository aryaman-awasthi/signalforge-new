"""The event hash chain and its seals.

Every event carries payload_hash (the canonical hash defined by the frozen
envelope contract) and prev_hash (the payload_hash of the event before it in the
same chain). Chains are per (tenant, plane): the signal plane ages out under
retention while the incident plane is kept forever, so a single chain across
both would be broken by the first prune. Pruning records an anchor - the last
pruned event's payload_hash - and verification of the surviving events starts
from it, so a prune is provable and a deletion is not.

Because prev_hash points at a payload hash, two adjacent events with byte-identical
payloads produce identical links. That is why sealing hashes the full ordered
tuple (seq, event_id, payload_hash, prev_hash): the seal binds position and
identity, not just content, and is what an audit is checked against.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class ChainLink:
    """One event's position in its (tenant, plane) chain."""

    seq: int
    event_id: str
    payload_hash: str
    prev_hash: Optional[str]


def verify_links(
    links: Sequence[ChainLink], anchor_hash: Optional[str] = None
) -> tuple[int, ...]:
    """Returns the seqs whose prev link does not match their predecessor.

    anchor_hash is the payload_hash of the last event pruned by retention, or
    None when nothing has ever been pruned from this chain (then the first link
    must be a genesis link with prev_hash None).
    """
    broken: list[int] = []
    expected_prev = anchor_hash
    for index, link in enumerate(links):
        if index == 0:
            if link.prev_hash != anchor_hash:
                broken.append(link.seq)
        elif link.prev_hash != expected_prev:
            broken.append(link.seq)
        expected_prev = link.payload_hash
    return tuple(broken)


def seal_hash(links: Sequence[ChainLink], prev_seal_hash: Optional[str] = None) -> str:
    """A sha256 over the ordered links, chained onto the previous seal so seals
    themselves form a chain: re-sealing a doctored range cannot reproduce it."""
    hasher = hashlib.sha256()
    hasher.update((prev_seal_hash or "").encode("utf-8"))
    for link in links:
        hasher.update(
            f"\n{link.seq}:{link.event_id}:{link.payload_hash}:{link.prev_hash or ''}".encode(
                "utf-8"
            )
        )
    return hasher.hexdigest()
