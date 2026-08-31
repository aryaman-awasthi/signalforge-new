"""Which adapters this process can instantiate (REQ-030).

A manifest and a runtime are separate things on purpose. The manifest is data
and travels with the adapter folder; the runtime is code and only exists where
someone wrote it. That split is what lets the registry honestly show an
enterprise connector as Available - interface known, nothing installed here -
instead of either hiding it or pretending it works.

Adapter runtimes register themselves into an instance of this class at
composition time (deps.Container). Nothing else may hold one, because building
an adapter needs decrypted credentials and those must not leak into a cache.
"""

from __future__ import annotations

from typing import Callable, Mapping

from signalforge.contracts.spi import Adapter
from signalforge.domain.adapters.errors import AdapterNotFoundError
from signalforge.domain.adapters.ports import AdapterContext, AdapterRuntimes

AdapterFactory = Callable[[AdapterContext], Adapter]


class AdapterRuntimeRegistry(AdapterRuntimes):
    def __init__(self, factories: Mapping[str, AdapterFactory] | None = None):
        self._factories: dict[str, AdapterFactory] = dict(factories or {})

    def register(self, adapter_id: str, factory: AdapterFactory) -> None:
        if adapter_id in self._factories:
            raise ValueError(f"a runtime for {adapter_id!r} is already registered")
        self._factories[adapter_id] = factory

    @property
    def installed(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def has(self, adapter_id: str) -> bool:
        return adapter_id in self._factories

    def build(self, context: AdapterContext) -> Adapter:
        factory = self._factories.get(context.manifest.id)
        if factory is None:
            raise AdapterNotFoundError(
                f"no runtime for adapter {context.manifest.id!r} is installed on this node"
            )
        adapter = factory(context)
        if adapter.kind is not context.manifest.kind:
            # The manifest is what the rest of the platform reasons about, so a
            # runtime whose SPI kind disagrees with it is a wiring bug that must
            # not reach a poller.
            raise ValueError(
                f"adapter {context.manifest.id!r} declares kind "
                f"{context.manifest.kind.value!r} but its runtime is a "
                f"{adapter.kind.value} adapter"
            )
        return adapter
