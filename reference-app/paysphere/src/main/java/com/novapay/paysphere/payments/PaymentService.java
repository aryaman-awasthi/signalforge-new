package com.novapay.paysphere.payments;

import java.time.Clock;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

import com.novapay.paysphere.HarnessProperties;
import com.novapay.paysphere.fabric.FabricSimulator;
import com.novapay.paysphere.ledger.Ledger;
import com.novapay.paysphere.ledger.PaymentRow;
import com.novapay.paysphere.ledger.PaymentState;
import com.novapay.paysphere.observe.LogStore;
import com.novapay.paysphere.observe.MetricsRegistry;
import com.novapay.paysphere.observe.RollingWindow;
import com.novapay.paysphere.observe.TraceStore;
import com.novapay.paysphere.runtimeconfig.ConfigRegistry;

import org.springframework.stereotype.Service;

/**
 * Placing a payment: the one thing this application actually does.
 *
 * <p>Everything the platform later reasons about is produced here and nowhere else - the counter,
 * the latency, the log line, the trace and the ledger row all come from the same call, so they
 * cannot disagree about what happened. Three behaviours are deliberate:
 *
 * <ul>
 *   <li>A payment lost at the rail is <b>UNKNOWN</b>, never FAILED. The reconciliation job is what
 *       resolves it; retrying it blindly would be a second payment.
 *   <li>Synthetic traffic is counted in its own series and never in the business counters, because
 *       a platform that probes an application and then watches the business KPI it inflated is
 *       measuring itself.
 *   <li>Injected latency is really slept, not merely reported. A latency fault that only moved a
 *       number would leave a synthetic probe cheerfully fast while the metric screamed, and the
 *       corroboration the platform is supposed to demonstrate would be a lie.
 * </ul>
 */
@Service
public class PaymentService {

    /** Sleeping the whole of a large injected latency would wedge the harness; this is the cap. */
    private static final long MAX_REAL_SLEEP_MS = 1500L;

    private static final int WINDOW = 200;
    private static final String SERVICE_GATEWAY = "payment-gateway";
    private static final String SERVICE_AGGREGATOR = "payment-aggregator";
    private static final String SERVICE_RAIL = "settlement-rail";

    private final FabricSimulator fabric;
    private final Ledger ledger;
    private final ConfigRegistry config;
    private final ProcessorLicense license;
    private final MetricsRegistry metrics;
    private final LogStore logs;
    private final TraceStore traces;
    private final Clock clock;
    private final Random random;

    private final Map<String, Integer> throttlePercent = new ConcurrentHashMap<>();
    private final Map<String, AtomicInteger> inFlight = new ConcurrentHashMap<>();
    private final Map<String, RollingWindow> windows = new ConcurrentHashMap<>();

    public PaymentService(
            FabricSimulator fabric,
            Ledger ledger,
            ConfigRegistry config,
            ProcessorLicense license,
            MetricsRegistry metrics,
            LogStore logs,
            TraceStore traces,
            Clock clock,
            HarnessProperties properties) {
        this.fabric = fabric;
        this.ledger = ledger;
        this.config = config;
        this.license = license;
        this.metrics = metrics;
        this.logs = logs;
        this.traces = traces;
        this.clock = clock;
        this.random = new Random(properties.getTrafficgen().getSeed());
    }

    /**
     * {@code errorCode} is the machine-readable code an upstream refusal carried, or null. It is
     * separate from {@code reason} because a status code is chosen from it: a fabric drop is a 503,
     * a refusal by the processor's license is the 403 the processor answered with.
     */
    public record Outcome(String paymentId, PaymentState state, double latencyMs, String traceId,
            String route, String reason, String errorCode) {

        public boolean customerVisibleFailure() {
            return state == PaymentState.FAILED || state == PaymentState.UNKNOWN;
        }
    }

    public int throttlePercent(String region) {
        return throttlePercent.getOrDefault(region, 0);
    }

    public void setThrottlePercent(String region, int percent) {
        throttlePercent.put(region, Math.max(0, Math.min(90, percent)));
    }

    public RollingWindow window(String region, Journey journey) {
        return windows.computeIfAbsent(region + "/" + journey.id(), key -> new RollingWindow(WINDOW));
    }

    public int inFlight(String region) {
        return inFlight.computeIfAbsent(region, key -> new AtomicInteger()).get();
    }

    public Outcome place(Journey journey, String region, long amountMinor, boolean synthetic) {
        String paymentId = "pay_" + UUID.randomUUID().toString().replace("-", "").substring(0, 20);
        String traceId = UUID.randomUUID().toString().replace("-", "");
        OffsetDateTime createdAt = OffsetDateTime.now(clock);
        Instant spanStart = Instant.now(clock);

        int throttle = throttlePercent(region);
        if (throttle > 0 && random.nextInt(100) < throttle) {
            return shed(journey, region, amountMinor, synthetic, paymentId, traceId, createdAt);
        }

        // Before the fabric is even touched: the processor SDK checks its license first and refuses
        // outright when it has lapsed. Nothing downstream of this line sees the request.
        if (license.expired()) {
            return refusedByLicense(
                    journey, region, amountMinor, synthetic, paymentId, traceId, createdAt, spanStart);
        }

        AtomicInteger flight = inFlight.computeIfAbsent(region, key -> new AtomicInteger());
        int concurrent = flight.incrementAndGet();
        try {
            int poolSize = config.get(region).poolSize();
            double poolWaitMs = concurrent > poolSize ? (concurrent - poolSize) * 25.0 : 0.0;

            FabricSimulator.Transit transit = fabric.traverse(region, random);
            double latencyMs = transit.latencyMs() + poolWaitMs;
            sleepInjected(transit.path());

            PaymentState state = stateOf(journey, transit);
            String reason =
                    transit.failedLink() == null
                            ? null
                            : (transit.uncertain()
                                    ? "no answer from the settlement rail at " + transit.failedLink()
                                    : "fabric link " + transit.failedLink() + " dropped the payment");
            String route = String.join(">", transit.path());

            ledger.insertPayment(
                    new PaymentRow(
                            paymentId,
                            region,
                            journey.id(),
                            amountMinor,
                            "INR",
                            state,
                            synthetic,
                            traceId,
                            route,
                            latencyMs,
                            reason,
                            createdAt,
                            createdAt));
            if (state == PaymentState.SUCCESS) {
                ledger.insertSettlement(
                        "stl_" + paymentId.substring(4),
                        paymentId,
                        region,
                        "completed",
                        createdAt,
                        createdAt);
            } else if (state == PaymentState.PENDING) {
                ledger.insertSettlement(
                        "stl_" + paymentId.substring(4), paymentId, region, "pending", createdAt, null);
            }

            writeSpans(traceId, journey, region, transit, spanStart, latencyMs, state);
            writeLog(paymentId, traceId, journey, region, state, reason, latencyMs, synthetic);
            countOutcome(journey, region, state, latencyMs, synthetic);

            return new Outcome(paymentId, state, latencyMs, traceId, route, reason, null);
        } finally {
            flight.decrementAndGet();
        }
    }

    /**
     * The processor SDK refusing a request because its license has lapsed.
     *
     * <p>Deliberately nothing like a fabric failure: no route is walked, no latency accrues, no
     * payment reaches the rail. The ledger row, the log line, the span and the counters all say the
     * same thing - refused with {@code LICENSE_EXPIRED} - because the platform has to be able to name
     * this cause from the application's own words rather than from the shape of a metric. It is a
     * FAILED customer outcome all the same, so the success ratio and the probes show it the way a
     * customer sees it.
     */
    private Outcome refusedByLicense(
            Journey journey,
            String region,
            long amountMinor,
            boolean synthetic,
            String paymentId,
            String traceId,
            OffsetDateTime createdAt,
            Instant spanStart) {
        double latencyMs = 3.0 + random.nextInt(5);
        String reason = license.refusalDetail();
        ledger.insertPayment(
                new PaymentRow(
                        paymentId,
                        region,
                        journey.id(),
                        amountMinor,
                        "INR",
                        PaymentState.FAILED,
                        synthetic,
                        traceId,
                        ProcessorLicense.PROCESSOR,
                        latencyMs,
                        reason,
                        createdAt,
                        createdAt));

        Map<String, String> attributes = new LinkedHashMap<>();
        attributes.put("region", region);
        attributes.put("journey", journey.id());
        attributes.put("payment_id", paymentId);
        attributes.put("state", PaymentState.FAILED.name());
        attributes.put("error_type", "license_expired");
        attributes.put("error_code", ProcessorLicense.ERROR_CODE);
        attributes.put("component", ProcessorLicense.COMPONENT);
        attributes.put("upstream_status", "403");
        attributes.put("synthetic", Boolean.toString(synthetic));
        logs.record(
                "error",
                SERVICE_GATEWAY,
                "payments",
                "payment " + paymentId + " refused in " + region + ": " + ProcessorLicense.ERROR_CODE
                        + " - " + reason,
                traceId,
                attributes);

        traces.record(
                new TraceStore.Span(
                        traceId,
                        shortId(),
                        null,
                        SERVICE_GATEWAY,
                        "POST /api/payments/" + journey.id(),
                        spanStart,
                        spanStart.plusMillis((long) latencyMs),
                        TraceStore.ERROR,
                        labels(
                                "region", region,
                                "journey", journey.id(),
                                "error_code", ProcessorLicense.ERROR_CODE)));

        countOutcome(journey, region, PaymentState.FAILED, latencyMs, synthetic);
        if (!synthetic) {
            metrics.increment(
                    "payment_rejections_total",
                    labels("region", region, "journey", journey.id(), "reason", "license_expired"),
                    1);
        }
        return new Outcome(
                paymentId,
                PaymentState.FAILED,
                latencyMs,
                traceId,
                ProcessorLicense.PROCESSOR,
                reason,
                ProcessorLicense.ERROR_CODE);
    }

    private Outcome shed(
            Journey journey,
            String region,
            long amountMinor,
            boolean synthetic,
            String paymentId,
            String traceId,
            OffsetDateTime createdAt) {
        ledger.insertPayment(
                new PaymentRow(
                        paymentId,
                        region,
                        journey.id(),
                        amountMinor,
                        "INR",
                        PaymentState.FAILED,
                        synthetic,
                        traceId,
                        "throttled",
                        0.0,
                        "shed by an operator throttle",
                        createdAt,
                        createdAt));
        logs.record(
                "warning",
                SERVICE_GATEWAY,
                "payments",
                "payment " + paymentId + " shed by throttle in " + region,
                traceId,
                Map.of("region", region, "journey", journey.id(), "payment_id", paymentId));
        // Counted apart from ordinary failures on purpose: a payment the operators chose to shed is
        // not evidence that the application is broken, and folding the two together would make a
        // remediation look like a regression.
        metrics.increment(
                "payment_requests_total",
                labels("region", region, "journey", journey.id(), "status", "throttled"),
                1);
        return new Outcome(paymentId, PaymentState.FAILED, 0.0, traceId, "throttled",
                "shed by an operator throttle", null);
    }

    private PaymentState stateOf(Journey journey, FabricSimulator.Transit transit) {
        if (transit.uncertain()) {
            return PaymentState.UNKNOWN;
        }
        if (!transit.ok()) {
            return PaymentState.FAILED;
        }
        // Refunds settle asynchronously at the bank, so a share of them are legitimately still open
        // when the API answers. Real PENDING traffic is what stops "pending" being read as a fault.
        if (journey == Journey.REFUND && random.nextInt(100) < 20) {
            return PaymentState.PENDING;
        }
        return PaymentState.SUCCESS;
    }

    private void sleepInjected(List<String> path) {
        double injected = 0.0;
        for (String linkId : path) {
            injected += fabric.link(linkId).injectedLatencyMs();
        }
        long sleepMs = (long) Math.min(injected, MAX_REAL_SLEEP_MS);
        if (sleepMs <= 0) {
            return;
        }
        try {
            Thread.sleep(sleepMs);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    private void writeSpans(
            String traceId,
            Journey journey,
            String region,
            FabricSimulator.Transit transit,
            Instant start,
            double latencyMs,
            PaymentState state) {
        String status = state == PaymentState.FAILED || state == PaymentState.UNKNOWN
                ? TraceStore.ERROR
                : TraceStore.OK;
        Instant end = start.plusMillis((long) latencyMs);
        Map<String, String> attributes =
                labels("region", region, "journey", journey.id(), "route", String.join(">", transit.path()));

        String rootSpan = shortId();
        traces.record(
                new TraceStore.Span(
                        traceId,
                        rootSpan,
                        null,
                        SERVICE_GATEWAY,
                        "POST /api/payments/" + journey.id(),
                        start,
                        end,
                        status,
                        attributes));
        String aggregatorSpan = shortId();
        traces.record(
                new TraceStore.Span(
                        traceId,
                        aggregatorSpan,
                        rootSpan,
                        SERVICE_AGGREGATOR,
                        "authorise",
                        start.plusMillis(2),
                        end.minusMillis(Math.min(10, (long) latencyMs)),
                        status,
                        attributes));
        if (transit.path().contains(FabricSimulator.RAIL)) {
            traces.record(
                    new TraceStore.Span(
                            traceId,
                            shortId(),
                            aggregatorSpan,
                            SERVICE_RAIL,
                            "settle",
                            start.plusMillis(5),
                            end,
                            status,
                            attributes));
        }
    }

    private void writeLog(
            String paymentId,
            String traceId,
            Journey journey,
            String region,
            PaymentState state,
            String reason,
            double latencyMs,
            boolean synthetic) {
        String level =
                switch (state) {
                    case FAILED -> "error";
                    case UNKNOWN -> "error";
                    case PENDING -> "info";
                    default -> "info";
                };
        Map<String, String> attributes = new LinkedHashMap<>();
        attributes.put("region", region);
        attributes.put("journey", journey.id());
        attributes.put("payment_id", paymentId);
        attributes.put("state", state.name());
        attributes.put("latency_ms", Long.toString((long) latencyMs));
        attributes.put("synthetic", Boolean.toString(synthetic));
        if (reason != null) {
            attributes.put("error_type", state == PaymentState.UNKNOWN ? "rail_no_answer" : "fabric_drop");
        }
        String message =
                reason == null
                        ? "payment " + paymentId + " " + state.name().toLowerCase() + " in " + region
                        : "payment " + paymentId + " " + state.name().toLowerCase() + " in " + region
                                + ": " + reason;
        logs.record(level, SERVICE_GATEWAY, "payments", message, traceId, attributes);
    }

    private void countOutcome(
            Journey journey, String region, PaymentState state, double latencyMs, boolean synthetic) {
        String status = state.name().toLowerCase();
        Map<String, String> outcomeLabels =
                labels("region", region, "journey", journey.id(), "status", status);
        if (synthetic) {
            metrics.increment("synthetic_requests_total", outcomeLabels, 1);
            return;
        }
        metrics.increment("payment_requests_total", outcomeLabels, 1);
        Map<String, String> pair = labels("region", region, "journey", journey.id());
        metrics.increment("payment_latency_ms_sum", pair, latencyMs);
        metrics.increment("payment_latency_ms_count", pair, 1);
        if (state == PaymentState.SUCCESS) {
            metrics.increment("settlements_completed_total", labels("region", region), 1);
        }
        window(region, journey).add(state == PaymentState.SUCCESS || state == PaymentState.PENDING,
                latencyMs);
    }

    private static String shortId() {
        return UUID.randomUUID().toString().replace("-", "").substring(0, 16);
    }

    private static Map<String, String> labels(String... pairs) {
        Map<String, String> labels = new LinkedHashMap<>();
        for (int i = 0; i + 1 < pairs.length; i += 2) {
            labels.put(pairs[i], pairs[i + 1]);
        }
        return labels;
    }
}
