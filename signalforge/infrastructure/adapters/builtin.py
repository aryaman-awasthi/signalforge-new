"""Which adapters this build ships a runtime for (REQ-031, REQ-033, REQ-035).

This is the only file in the platform that names adapters, and it lives beside
them in the adapters tree rather than in the core, which is the whole point of
the split: `api`, `application` and `domain` contain no adapter id anywhere, and
none of them contains an application name either.

A manifest without an entry here is `available` - interface known, nothing
installed - and the registry, the capability matrix and the health loop all say
so. That is how the five enterprise connectors are represented, and adding a
real runtime for one of them is an entry in this table plus a manifest field.
"""

from __future__ import annotations

from typing import Callable, Mapping

from signalforge.contracts.spi import Adapter
from signalforge.domain.adapters.ports import AdapterContext
from signalforge.infrastructure.adapters.itsm_lite.adapter import ItsmLiteAdapter
from signalforge.infrastructure.adapters.kb_index.adapter import KbIndexAdapter
from signalforge.infrastructure.adapters.notify.adapters import (
    BrowserPushNotifyAdapter,
    InAppNotifyAdapter,
    PageSimNotifyAdapter,
)
from signalforge.infrastructure.adapters.observe.adapters import (
    ObserveActionsAdapter,
    ObserveChaosAdapter,
    ObserveHealthAdapter,
    ObserveLogsAdapter,
    ObserveMetricsAdapter,
    ObserveTracesAdapter,
)
from signalforge.infrastructure.adapters.prometheus.adapter import PrometheusAdapter
from signalforge.infrastructure.adapters.services import AdapterServices
from signalforge.infrastructure.adapters.teams_sim.adapters import (
    TeamsSimCollaborationAdapter,
    TeamsSimDmAdapter,
)
from signalforge.infrastructure.adapters.topo_import.adapter import TopoImportAdapter
from signalforge.infrastructure.adapters.webhooks.adapters import (
    DeployWebhookAdapter,
    GrafanaAlertWebhookAdapter,
)

BUILTIN_RUNTIMES: Mapping[str, type] = {
    "http-observe-metrics": ObserveMetricsAdapter,
    "http-observe-logs": ObserveLogsAdapter,
    "http-observe-traces": ObserveTracesAdapter,
    "http-observe-health": ObserveHealthAdapter,
    "http-observe-actions": ObserveActionsAdapter,
    "http-observe-chaos": ObserveChaosAdapter,
    "prometheus": PrometheusAdapter,
    "grafana-alert-webhook": GrafanaAlertWebhookAdapter,
    "deploy-webhook": DeployWebhookAdapter,
    "topo-import": TopoImportAdapter,
    "itsm-lite": ItsmLiteAdapter,
    "teams-sim": TeamsSimCollaborationAdapter,
    "teams-sim-dm": TeamsSimDmAdapter,
    "notify-in-app": InAppNotifyAdapter,
    "notify-browser-push": BrowserPushNotifyAdapter,
    "page-sim": PageSimNotifyAdapter,
    "kb-index": KbIndexAdapter,
}


def make_factory(
    adapter_class: type, services: AdapterServices
) -> Callable[[AdapterContext], Adapter]:
    """A factory builds a *new* adapter per call, closing over the services and
    never over the credentials: those arrive on the context and live only as
    long as the instance."""

    def factory(context: AdapterContext) -> Adapter:
        return adapter_class(context, services)

    return factory


def register_builtin_runtimes(runtimes, services: AdapterServices) -> tuple[str, ...]:
    for adapter_id, adapter_class in BUILTIN_RUNTIMES.items():
        runtimes.register(adapter_id, make_factory(adapter_class, services))
    return tuple(BUILTIN_RUNTIMES)
