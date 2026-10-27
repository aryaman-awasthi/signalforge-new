package com.novapay.paysphere.observe;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.novapay.paysphere.fabric.FabricSimulator;
import com.novapay.paysphere.ledger.Ledger;
import com.novapay.paysphere.ledger.PaymentState;
import com.novapay.paysphere.payments.Journey;
import com.novapay.paysphere.payments.PaymentService;
import com.novapay.paysphere.runtimeconfig.ConfigRegistry;

import jakarta.annotation.PostConstruct;

import org.springframework.stereotype.Component;

/**
 * Declares every metric the harness exports and refreshes the computed ones on each scrape.
 *
 * <p>The split matters. Counters are incremented where the event happens, because a counter that is
 * recomputed at scrape time is not a counter. Gauges - the success ratio, the p95, the backlog, the
 * fabric state - are read at scrape time, because they describe how things stand now and computing
 * them earlier would report a state that has already moved.
 *
 * <p>Every series is registered at zero at startup. Detection that cannot tell "no errors" from "no
 * data" will eventually treat a dead exporter as a healthy service.
 */
@Component
public class MetricsPublisher {

    private static final List<String> OUTCOMES =
            List.of("success", "pending", "failed", "unknown", "throttled");

    private final MetricsRegistry metrics;
    private final PaymentService payments;
    private final FabricSimulator fabric;
    private final ConfigRegistry config;
    private final Ledger ledger;

    public MetricsPublisher(
            MetricsRegistry metrics,
            PaymentService payments,
            FabricSimulator fabric,
            ConfigRegistry config,
            Ledger ledger) {
        this.metrics = metrics;
        this.payments = payments;
        this.fabric = fabric;
        this.config = config;
        this.ledger = ledger;
    }

    @PostConstruct
    void declare() {
        metrics.declareCounter(
                "payment_requests_total",
                "Customer payment requests by outcome. Synthetic traffic is not counted here.",
                "region",
                "journey",
                "status");
        metrics.declareCounter(
                "synthetic_requests_total",
                "Synthetic probe payments by outcome, kept out of the business counters.",
                "region",
                "journey",
                "status");
        metrics.declareCounter(
                "payment_latency_ms_sum",
                "Total observed payment latency in milliseconds.",
                "region",
                "journey");
        metrics.declareCounter(
                "payment_latency_ms_count",
                "Number of payments contributing to payment_latency_ms_sum.",
                "region",
                "journey");
        metrics.declareCounter(
                "settlements_completed_total",
                "Settlements completed. This is the business counter the money is measured by.",
                "region");
        metrics.declareCounter(
                "http_requests_total",
                "HTTP requests served by this application.",
                "endpoint",
                "method",
                "status");

        metrics.declareGauge(
                "payment_success_ratio",
                "Share of the last 200 payments that succeeded or are legitimately pending.",
                "region",
                "journey");
        metrics.declareGauge(
                "payment_latency_p95_ms",
                "Nearest-rank p95 over the last 200 payment latencies. Never interpolated.",
                "region",
                "journey");
        metrics.declareGauge(
                "payments_unknown",
                "Payments whose outcome the rail never confirmed. This is the reconciliation backlog.",
                "region");
        metrics.declareGauge(
                "settlement_backlog", "Settlements created and not yet completed.", "region");
        metrics.declareGauge(
                "db_pool_size", "Configured ledger connection pool size.", "region");
        metrics.declareGauge(
                "db_pool_in_use", "Payments currently holding a ledger connection.", "region");
        metrics.declareGauge("config_version", "Applied runtime configuration version.", "region");
        metrics.declareGauge("throttle_percent", "Percentage of inbound payments being shed.", "region");
        metrics.declareGauge("fabric_link_up", "1 when the fabric link is carrying traffic.", "link");
        metrics.declareGauge(
                "fabric_link_latency_ms", "Current one-way latency of the fabric link.", "link");
        metrics.declareGauge(
                "fabric_link_loss_ratio", "Share of payments the fabric link is dropping.", "link");
        metrics.declareGauge(
                "chaos_injections_active", "Chaos injections currently applied to this application.");

        for (String region : config.regions()) {
            for (Journey journey : Journey.values()) {
                for (String outcome : OUTCOMES) {
                    metrics.preregister(
                            "payment_requests_total",
                            labels("region", region, "journey", journey.id(), "status", outcome));
                    metrics.preregister(
                            "synthetic_requests_total",
                            labels("region", region, "journey", journey.id(), "status", outcome));
                }
                metrics.preregister(
                        "payment_latency_ms_sum", labels("region", region, "journey", journey.id()));
                metrics.preregister(
                        "payment_latency_ms_count", labels("region", region, "journey", journey.id()));
            }
            metrics.preregister("settlements_completed_total", labels("region", region));
        }
    }

    /** Called by the metrics endpoint immediately before the exposition is rendered. */
    public void refresh() {
        for (String region : config.regions()) {
            Map<String, String> regionLabel = labels("region", region);
            for (Journey journey : Journey.values()) {
                RollingWindow window = payments.window(region, journey);
                Map<String, String> pair = labels("region", region, "journey", journey.id());
                metrics.setGauge("payment_success_ratio", pair, window.successRatio());
                metrics.setGauge("payment_latency_p95_ms", pair, window.percentileMs(0.95));
            }
            metrics.setGauge(
                    "payments_unknown", regionLabel, ledger.countPayments(region, PaymentState.UNKNOWN));
            metrics.setGauge(
                    "settlement_backlog", regionLabel, ledger.countSettlements(region, "pending"));
            metrics.setGauge("db_pool_size", regionLabel, config.get(region).poolSize());
            metrics.setGauge("db_pool_in_use", regionLabel, payments.inFlight(region));
            metrics.setGauge("config_version", regionLabel, config.get(region).version());
            metrics.setGauge("throttle_percent", regionLabel, payments.throttlePercent(region));
        }
        for (FabricSimulator.Link link : fabric.links()) {
            Map<String, String> linkLabel = labels("link", link.id());
            metrics.setGauge("fabric_link_up", linkLabel, link.up() ? 1 : 0);
            metrics.setGauge("fabric_link_latency_ms", linkLabel, link.latencyMs());
            metrics.setGauge("fabric_link_loss_ratio", linkLabel, link.lossRatio());
        }
        metrics.setGauge("chaos_injections_active", Map.of(), ledger.injectionsInState("active").size());
    }

    public void countHttp(String endpoint, String method, int status) {
        metrics.increment(
                "http_requests_total",
                labels("endpoint", endpoint, "method", method, "status", Integer.toString(status)),
                1);
    }

    private static Map<String, String> labels(String... pairs) {
        Map<String, String> labels = new LinkedHashMap<>();
        for (int i = 0; i + 1 < pairs.length; i += 2) {
            labels.put(pairs[i], pairs[i + 1]);
        }
        return labels;
    }
}
