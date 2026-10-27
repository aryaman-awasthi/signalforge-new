package com.novapay.paysphere.observe;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

import com.novapay.paysphere.actions.ActionCatalog;
import com.novapay.paysphere.actions.ActionExecutor;
import com.novapay.paysphere.chaos.ChaosCatalog;
import com.novapay.paysphere.chaos.ChaosEngine;

import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * The whole of the frozen {@code /observe} contract, and nothing else.
 *
 * <p>This class is the reason the harness exists. Every path, every query parameter and every
 * response shape below comes from {@code signalforge/contracts/observe.py}; none of it was designed
 * for this application, and none of it names the platform. An application that serves these twelve
 * paths can be onboarded by the generic http-observe adapters with no code written for it on either
 * side - which is the claim the whole sprint is here to test.
 */
@RestController
public class ObserveController {

    /** Frozen in contracts/observe.py as OBSERVE_METRICS_CONTENT_TYPE. */
    public static final String METRICS_CONTENT_TYPE = "text/plain; version=0.0.4";

    private static final int DEFAULT_LIMIT = 100;

    public record LogsResponse(List<LogStore.Entry> records, Integer total) {}

    public record TracesResponse(List<TraceStore.Span> spans) {}

    public record ExecuteRequest(Map<String, Object> params, String idempotencyKey) {}

    public record InjectRequest(Map<String, Object> params, String scope, String idempotencyKey) {}

    private final MetricsRegistry metrics;
    private final MetricsPublisher publisher;
    private final LogStore logs;
    private final TraceStore traces;
    private final HealthService health;
    private final TopologyService topology;
    private final ActionCatalog actionCatalog;
    private final ActionExecutor actions;
    private final ChaosCatalog chaosCatalog;
    private final ChaosEngine chaos;

    public ObserveController(
            MetricsRegistry metrics,
            MetricsPublisher publisher,
            LogStore logs,
            TraceStore traces,
            HealthService health,
            TopologyService topology,
            ActionCatalog actionCatalog,
            ActionExecutor actions,
            ChaosCatalog chaosCatalog,
            ChaosEngine chaos) {
        this.metrics = metrics;
        this.publisher = publisher;
        this.logs = logs;
        this.traces = traces;
        this.health = health;
        this.topology = topology;
        this.actionCatalog = actionCatalog;
        this.actions = actions;
        this.chaosCatalog = chaosCatalog;
        this.chaos = chaos;
    }

    // --- telemetry -----------------------------------------------------------------------

    @GetMapping("/observe/metrics")
    public ResponseEntity<String> metrics() {
        publisher.refresh();
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_TYPE, METRICS_CONTENT_TYPE)
                .body(metrics.scrape());
    }

    @GetMapping("/observe/logs")
    public LogsResponse logs(
            @RequestParam(name = "query", required = false) String query,
            @RequestParam(name = "start", required = false) String start,
            @RequestParam(name = "end", required = false) String end,
            @RequestParam(name = "limit", defaultValue = "" + DEFAULT_LIMIT) int limit) {
        Instant from = instant(start);
        Instant to = instant(end);
        List<LogStore.Entry> records = logs.search(query, from, to, Math.max(0, limit));
        return new LogsResponse(records, logs.total(query, from, to));
    }

    @GetMapping("/observe/traces")
    public TracesResponse traces(
            @RequestParam(name = "trace_id", required = false) String traceId,
            @RequestParam(name = "service", required = false) String service,
            @RequestParam(name = "operation", required = false) String operation,
            @RequestParam(name = "errors_only", defaultValue = "false") boolean errorsOnly,
            @RequestParam(name = "start", required = false) String start,
            @RequestParam(name = "end", required = false) String end,
            @RequestParam(name = "limit", defaultValue = "" + DEFAULT_LIMIT) int limit) {
        if (traceId != null && !traceId.isBlank()) {
            // Whole-trace fetch: a trace is only useful complete, so the window and the limit do
            // not apply to it.
            return new TracesResponse(traces.byTrace(traceId));
        }
        return new TracesResponse(
                traces.search(
                        instant(start), instant(end), service, operation, errorsOnly, Math.max(0, limit)));
    }

    @GetMapping("/observe/health")
    public HealthService.Report health() {
        return health.report();
    }

    @GetMapping("/observe/topology")
    public TopologyService.Snapshot topology() {
        return topology.snapshot();
    }

    // --- actions (remediation credential) ------------------------------------------------

    @GetMapping("/observe/actions")
    public Map<String, Object> actionsManifest() {
        return actionCatalog.document();
    }

    @PostMapping("/observe/actions/{action_id}/execute")
    public ActionExecutor.Execution execute(
            @PathVariable("action_id") String actionId, @RequestBody ExecuteRequest request) {
        if (request == null || request.idempotencyKey() == null || request.idempotencyKey().isBlank()) {
            throw new IllegalArgumentException("idempotency_key is required to execute an action");
        }
        return actions.execute(actionId, request.params(), request.idempotencyKey());
    }

    @GetMapping("/observe/actions/executions/{idempotency_key}")
    public ResponseEntity<ActionExecutor.Execution> execution(
            @PathVariable("idempotency_key") String idempotencyKey) {
        // 404 means "this key never ran", which is what makes it a usable reconcile answer. It has
        // to be distinguishable from an execution that ran and failed.
        return actions.find(idempotencyKey).map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    // --- chaos (drill credential) --------------------------------------------------------

    @GetMapping("/observe/chaos")
    public Map<String, Object> chaosManifest() {
        return chaosCatalog.document();
    }

    @PostMapping("/observe/chaos/{scenario_id}/inject")
    public ChaosEngine.Injection inject(
            @PathVariable("scenario_id") String scenarioId, @RequestBody InjectRequest request) {
        if (request == null || request.idempotencyKey() == null || request.idempotencyKey().isBlank()) {
            throw new IllegalArgumentException("idempotency_key is required to inject a fault");
        }
        return chaos.inject(scenarioId, request.params(), request.scope(), request.idempotencyKey());
    }

    /** Active injections, for a console that has to show what is applied and abort it. */
    @GetMapping("/observe/chaos/injections")
    public Map<String, Object> activeInjections() {
        return Map.of("injections", chaos.active());
    }

    @GetMapping("/observe/chaos/injections/{injection_id}")
    public ChaosEngine.Injection injectionStatus(@PathVariable("injection_id") String injectionId) {
        return chaos.status(injectionId);
    }

    @PostMapping("/observe/chaos/injections/{injection_id}/abort")
    public ChaosEngine.Injection abort(@PathVariable("injection_id") String injectionId) {
        return chaos.abort(injectionId);
    }

    /**
     * Parse a caller's window bound. A {@code +} that survived URL decoding as a space is repaired
     * rather than refused: the offset is the commonest thing to get wrong on the wire and rejecting
     * it would make a correct client look broken.
     */
    static Instant instant(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        String text = raw.trim().replace(' ', '+');
        try {
            return OffsetDateTime.parse(text).toInstant();
        } catch (RuntimeException notAnOffset) {
            try {
                return Instant.parse(text);
            } catch (RuntimeException notAnInstant) {
                throw new IllegalArgumentException(
                        "not a timezone-aware ISO-8601 timestamp: " + raw);
            }
        }
    }
}
