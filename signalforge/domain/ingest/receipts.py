"""The ingest receipt: proof that this tenant has already seen this event_id.

The frozen contract makes `event_id` the idempotency unit, and a receipt is what
turns that sentence into a guarantee. It is stored, not derived: a sender whose
connection dropped before our 202 arrived has no way to know whether we recorded
its batch, and the only safe answer to the retry it will certainly send is to
acknowledge it without applying it twice.

The uniqueness key is **(tenant_id, event_id)**, not (key, event_id):

- the same fact posted through a key and its rotation replacement is one fact,
  so rotation cannot cause a double-apply;
- event ids are producer-assigned, so two tenants can and will pick the same
  one, and neither may block or observe the other.

The receipt also records which platform event the item became, so an operator
holding a sender's event_id can find what it produced without a full-text
search of the timeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from signalforge.domain.ingest.streams import IngestStream


@dataclass(frozen=True)
class IngestReceipt:
    tenant_id: str
    event_id: str
    """The producer's id, canonicalised. The idempotency unit."""
    key_id: str
    app_id: str
    stream: IngestStream
    source_system: str
    occurred_at: datetime
    received_at: datetime
    platform_event_id: str
    event_seq: int
    event_count: int = 1
    """One posted item can become several events - an alerts webhook batch
    normalises into one canonical alert per alert - and the count says so rather
    than the receipt implying a 1:1 mapping that does not hold."""
    simulated: bool = False
    normalized_by: Optional[str] = None
    """The adapter id whose `normalize` translated a vendor payload, or None
    when the sender posted the canonical shape directly."""
