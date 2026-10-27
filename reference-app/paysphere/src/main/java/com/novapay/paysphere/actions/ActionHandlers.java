package com.novapay.paysphere.actions;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.novapay.paysphere.fabric.FabricSimulator;
import com.novapay.paysphere.ledger.ConfigVersionRow;
import com.novapay.paysphere.ledger.Ledger;
import com.novapay.paysphere.ledger.PaymentRow;
import com.novapay.paysphere.ledger.PaymentState;
import com.novapay.paysphere.observe.LogStore;
import com.novapay.paysphere.payments.PaymentService;
import com.novapay.paysphere.payments.ProcessorLicense;
import com.novapay.paysphere.runtimeconfig.ConfigRegistry;

import org.springframework.stereotype.Component;

/**
 * What each published action actually does.
 *
 * <p>All seven are written to be safe to repeat, because the platform will repeat them: an execution
 * whose answer was lost is retried under the same idempotency key, and an action that only worked
 * the first time would turn a lost response into a second outage. Where repetition genuinely cannot
 * be made harmless - {@code restart-gateway} - the manifest says {@code idempotent: false} rather
 * than pretending.
 */
@Component
public class ActionHandlers {

    private final ConfigRegistry config;
    private final FabricSimulator fabric;
    private final PaymentService payments;
    private final ProcessorLicense license;
    private final Ledger ledger;
    private final LogStore logs;

    public ActionHandlers(
            ConfigRegistry config,
            FabricSimulator fabric,
            PaymentService payments,
            ProcessorLicense license,
            Ledger ledger,
            LogStore logs) {
        this.config = config;
        this.fabric = fabric;
        this.payments = payments;
        this.license = license;
        this.ledger = ledger;
        this.logs = logs;
    }

    public Map<String, ActionHandler> handlers() {
        Map<String, ActionHandler> handlers = new LinkedHashMap<>();
        handlers.put("read-config", this::readConfig);
        handlers.put("run-reconciliation-job", this::runReconciliation);
        handlers.put("rollback-config", this::rollbackConfig);
        handlers.put("failover-route", params -> setRoute(params, FabricSimulator.ROUTE_SECONDARY));
        handlers.put("restore-route", params -> setRoute(params, FabricSimulator.ROUTE_PRIMARY));
        handlers.put("throttle-traffic", this::throttleTraffic);
        handlers.put("restart-gateway", this::restartGateway);
        handlers.put("renew-license-key", this::renewLicenseKey);
        return handlers;
    }

    /**
     * Install a renewed processor license key. Idempotent in the only sense that matters: a second
     * renewal under the same key extends the same license rather than installing a second one, and
     * a renewal of a license that had not lapsed is harmless.
     */
    private Map<String, Object> renewLicenseKey(Map<String, Object> params) {
        int days = intOr(params.get("valid_days"), 365);
        boolean wasExpired = license.expired();
        Instant validUntil = license.renew(days);
        logs.record(
                "warning",
                "payment-gateway",
                "actions",
                "payment processor license key renewed; valid until " + validUntil
                        + (wasExpired ? " (the previous key had expired)" : ""),
                null,
                Map.of(
                        "component", ProcessorLicense.COMPONENT,
                        "valid_days", Integer.toString(days),
                        "was_expired", Boolean.toString(wasExpired)));
        return Map.of(
                "component", ProcessorLicense.COMPONENT,
                "was_expired", wasExpired,
                "valid_until", validUntil.toString(),
                "valid_days", days);
    }

    private Map<String, Object> readConfig(Map<String, Object> params) {
        String region = region(params);
        ConfigRegistry.RegionConfig current = config.get(region);
        List<Map<String, Object>> history = new ArrayList<>();
        for (ConfigVersionRow row : config.history(region)) {
            history.add(
                    Map.of(
                            "version", row.version(),
                            "settings", row.settings(),
                            "source", row.source(),
                            "applied_at", row.appliedAt().toString()));
        }
        return Map.of(
                "region", region,
                "version", current.version(),
                "settings", current.settings(),
                "history", history);
    }

    /**
     * Ask the rail what happened to every payment left UNKNOWN, then write down the answer.
     *
     * <p>The rail's answer is derived from the payment id, so it is the same answer every time the
     * job runs. That is what makes a second run a no-op instead of a coin toss - reconciliation that
     * could change its mind would be worse than no reconciliation at all.
     */
    private Map<String, Object> runReconciliation(Map<String, Object> params) {
        String region = region(params);
        int limit = intOr(params.get("limit"), 500);
        List<PaymentRow> unresolved = ledger.paymentsInState(region, PaymentState.UNKNOWN, limit);
        int settled = 0;
        int failed = 0;
        for (PaymentRow payment : unresolved) {
            boolean moneyMoved = Math.floorMod(payment.paymentId().hashCode(), 2) == 0;
            if (moneyMoved) {
                ledger.updatePaymentState(
                        payment.paymentId(), PaymentState.SUCCESS, "reconciled: the rail had applied it",
                        payment.updatedAt());
                ledger.insertSettlement(
                        "stl_" + payment.paymentId().substring(4),
                        payment.paymentId(),
                        region,
                        "completed",
                        payment.createdAt(),
                        payment.updatedAt());
                settled++;
            } else {
                ledger.updatePaymentState(
                        payment.paymentId(),
                        PaymentState.FAILED,
                        "reconciled: the rail never applied it",
                        payment.updatedAt());
                failed++;
            }
        }
        logs.record(
                "info",
                "reconciliation",
                "actions",
                "reconciled " + unresolved.size() + " unknown payments in " + region,
                null,
                Map.of("region", region, "settled", Integer.toString(settled),
                        "failed", Integer.toString(failed)));
        return Map.of(
                "region", region,
                "examined", unresolved.size(),
                "resolved_success", settled,
                "resolved_failed", failed,
                "remaining_unknown", ledger.countPayments(region, PaymentState.UNKNOWN));
    }

    private Map<String, Object> rollbackConfig(Map<String, Object> params) {
        String region = region(params);
        int target = intOr(params.get("to_version"), -1);
        if (target < 1) {
            throw new IllegalArgumentException("to_version must be a configuration version number");
        }
        int before = config.get(region).version();
        ConfigRegistry.RegionConfig applied = config.rollbackTo(region, target);
        logs.record(
                "warning",
                "config-registry",
                "actions",
                "rolled " + region + " back to configuration version " + target,
                null,
                Map.of("region", region, "from_version", Integer.toString(before),
                        "to_version", Integer.toString(target)));
        return Map.of(
                "region", region,
                "from_version", before,
                "requested_version", target,
                "new_version", applied.version(),
                "settings", applied.settings());
    }

    private Map<String, Object> setRoute(Map<String, Object> params, String route) {
        String region = region(params);
        boolean changed = fabric.setRoute(region, route);
        logs.record(
                "warning",
                "fabric",
                "actions",
                "region " + region + " is now on its " + route + " route",
                null,
                Map.of("region", region, "route", route, "changed", Boolean.toString(changed)));
        return Map.of(
                "region", region,
                "route", route,
                "changed", changed,
                "path", fabric.route(region),
                "routing_version", fabric.routingVersion());
    }

    private Map<String, Object> throttleTraffic(Map<String, Object> params) {
        String region = region(params);
        int percent = intOr(params.get("percent"), -1);
        if (percent < 0 || percent > 90) {
            throw new IllegalArgumentException("percent must be between 0 and 90");
        }
        int before = payments.throttlePercent(region);
        payments.setThrottlePercent(region, percent);
        logs.record(
                percent > 0 ? "warning" : "info",
                "payment-gateway",
                "actions",
                "inbound throttle for " + region + " set to " + percent + "%",
                null,
                Map.of("region", region, "percent", Integer.toString(percent)));
        return Map.of("region", region, "previous_percent", before, "percent", percent);
    }

    private Map<String, Object> restartGateway(Map<String, Object> params) throws InterruptedException {
        String region = region(params);
        String link = "region-b".equals(region) ? FabricSimulator.GW_B : FabricSimulator.GW_A;
        logs.record(
                "error",
                "payment-gateway",
                "actions",
                "restarting the " + region + " gateway; in-flight payments will be lost",
                null,
                Map.of("region", region, "link", link));
        fabric.setUp(link, false);
        try {
            Thread.sleep(1500L);
        } finally {
            fabric.setUp(link, true);
        }
        return Map.of("region", region, "link", link, "downtime_ms", 1500);
    }

    private String region(Map<String, Object> params) {
        Object raw = params.get("region");
        String region = raw == null ? null : String.valueOf(raw);
        if (region == null || !config.regions().contains(region)) {
            throw new IllegalArgumentException(
                    "region must be one of " + config.regions() + ", got " + raw);
        }
        return region;
    }

    private static int intOr(Object value, int fallback) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        if (value instanceof String text && !text.isBlank()) {
            try {
                return Integer.parseInt(text.trim());
            } catch (NumberFormatException ignored) {
                return fallback;
            }
        }
        return fallback;
    }
}
