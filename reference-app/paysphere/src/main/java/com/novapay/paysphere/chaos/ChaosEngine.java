package com.novapay.paysphere.chaos;

import java.time.Clock;
import java.time.OffsetDateTime;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

import com.novapay.paysphere.NotFoundException;
import com.novapay.paysphere.fabric.FabricSimulator;
import com.novapay.paysphere.ledger.InjectionRow;
import com.novapay.paysphere.ledger.Ledger;
import com.novapay.paysphere.observe.LogStore;
import com.novapay.paysphere.runtimeconfig.ConfigRegistry;

import jakarta.annotation.PostConstruct;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Applies, expires and reverts the published chaos scenarios.
 *
 * <p>Three rules are not negotiable here, and all three are about not leaving a fault behind:
 *
 * <ul>
 *   <li>Every injection carries an expiry taken from the scenario's own {@code max_duration_s}. A
 *       drill that ends badly, a controller that crashes or an operator who goes home does not leave
 *       the harness broken - the sweeper reverts it.
 *   <li>An injection is refused unless the caller presents a {@code drill:} scope. The platform
 *       already refuses chaos on a non-drill credential; the application refusing it too is what
 *       makes that a control rather than a convention.
 *   <li>On startup, any injection still marked active belongs to a process that is gone. The faults
 *       it applied went with it, so the row is closed rather than left claiming a fault that is no
 *       longer applied.
 * </ul>
 */
@Component
public class ChaosEngine {

    private static final Logger LOG = LoggerFactory.getLogger(ChaosEngine.class);

    public static final String ACTIVE = "active";
    public static final String COMPLETED = "completed";
    public static final String ABORTED = "aborted";
    public static final String DRILL_SCOPE_PREFIX = "drill:";

    /** Exactly the frozen ChaosInjection shape. */
    public record Injection(
            String injectionId,
            String scenarioId,
            String target,
            Map<String, Object> params,
            String state,
            String scope,
            OffsetDateTime startedAt,
            OffsetDateTime endedAt) {}

    private interface Fault {
        void apply(Map<String, Object> params);

        void revert(Map<String, Object> params);
    }

    private final ChaosCatalog catalog;
    private final FabricSimulator fabric;
    private final ConfigRegistry config;
    private final Ledger ledger;
    private final LogStore logs;
    private final Clock clock;
    private final Map<String, Fault> faults = new ConcurrentHashMap<>();
    private final Map<String, Integer> poolSizeBeforeShrink = new ConcurrentHashMap<>();

    public ChaosEngine(
            ChaosCatalog catalog,
            FabricSimulator fabric,
            ConfigRegistry config,
            Ledger ledger,
            LogStore logs,
            Clock clock) {
        this.catalog = catalog;
        this.fabric = fabric;
        this.config = config;
        this.ledger = ledger;
        this.logs = logs;
        this.clock = clock;
        registerFaults();
    }

    private void registerFaults() {
        faults.put(
                "fabric-link-loss",
                fault(
                        params -> fabric.setLoss(linkOf(params), doubleOf(params.get("loss_ratio"), 0.0)),
                        params -> fabric.setLoss(linkOf(params), 0.0)));
        faults.put(
                "rail-latency",
                fault(
                        params ->
                                fabric.setInjectedLatency(
                                        FabricSimulator.RAIL, doubleOf(params.get("latency_ms"), 0.0)),
                        params -> fabric.setInjectedLatency(FabricSimulator.RAIL, 0.0)));
        faults.put(
                "db-pool-shrink",
                fault(
                        params -> {
                            String region = regionOf(params);
                            poolSizeBeforeShrink.put(region, config.get(region).poolSize());
                            config.apply(
                                    region,
                                    Map.of("pool_size", (int) doubleOf(params.get("pool_size"), 4)),
                                    "chaos:db-pool-shrink");
                        },
                        params -> {
                            String region = regionOf(params);
                            Integer previous = poolSizeBeforeShrink.remove(region);
                            if (previous != null) {
                                config.apply(
                                        region, Map.of("pool_size", previous), "chaos:db-pool-shrink-revert");
                            }
                        }));
        faults.put(
                "rollback-delay",
                fault(
                        params -> config.setApplyDelayMs((long) doubleOf(params.get("delay_ms"), 0.0)),
                        params -> config.setApplyDelayMs(0L)));
        faults.put(
                "gateway-down",
                fault(
                        params -> fabric.setUp(linkOf(params), false),
                        params -> fabric.setUp(linkOf(params), true)));
    }

    private static Fault fault(
            java.util.function.Consumer<Map<String, Object>> onApply,
            java.util.function.Consumer<Map<String, Object>> onRevert) {
        return new Fault() {
            @Override
            public void apply(Map<String, Object> params) {
                onApply.accept(params);
            }

            @Override
            public void revert(Map<String, Object> params) {
                onRevert.accept(params);
            }
        };
    }

    @PostConstruct
    void assertManifestAndCodeAgree() {
        Set<String> published = catalog.scenarioIdSet();
        Set<String> implemented = new LinkedHashSet<>(faults.keySet());
        Set<String> unimplemented = new LinkedHashSet<>(published);
        unimplemented.removeAll(implemented);
        Set<String> unpublished = new LinkedHashSet<>(implemented);
        unpublished.removeAll(published);
        if (!unimplemented.isEmpty() || !unpublished.isEmpty()) {
            throw new IllegalStateException(
                    "chaos manifest and implementation disagree; published with no fault: "
                            + unimplemented
                            + ", implemented but not published: "
                            + unpublished);
        }
        closeInjectionsFromAPreviousProcess();
    }

    private void closeInjectionsFromAPreviousProcess() {
        for (InjectionRow row : ledger.injectionsInState(ACTIVE)) {
            ledger.endInjection(row.injectionId(), COMPLETED, OffsetDateTime.now(clock));
            LOG.info(
                    "closed injection {} left active by a previous process; its fault died with it",
                    row.injectionId());
        }
    }

    public Injection inject(
            String scenarioId, Map<String, Object> params, String scope, String idempotencyKey) {
        assertDrillScope(scope);
        Map<String, Object> scenario = catalog.scenario(scenarioId);
        Optional<InjectionRow> already = ledger.findInjectionByKey(idempotencyKey);
        if (already.isPresent()) {
            return toInjection(already.get());
        }
        OffsetDateTime now = OffsetDateTime.now(clock);
        InjectionRow row =
                new InjectionRow(
                        "inj_" + UUID.randomUUID().toString().replace("-", "").substring(0, 20),
                        idempotencyKey,
                        scenarioId,
                        String.valueOf(scenario.get("target")),
                        params == null ? Map.of() : params,
                        ACTIVE,
                        scope,
                        now.plusSeconds((long) catalog.maxDurationS(scenarioId)),
                        now,
                        null);
        if (!ledger.claimInjection(row)) {
            return toInjection(ledger.findInjectionByKey(idempotencyKey).orElseThrow());
        }
        faults.get(scenarioId).apply(row.params());
        logs.record(
                "warning",
                "chaos",
                "chaos",
                "injected " + scenarioId + " under " + scope,
                null,
                Map.of("scenario_id", scenarioId, "injection_id", row.injectionId(), "scope", scope));
        return toInjection(row);
    }

    public Injection status(String injectionId) {
        return toInjection(
                ledger.findInjection(injectionId)
                        .orElseThrow(
                                () -> new NotFoundException("no such injection: " + injectionId)));
    }

    public Injection abort(String injectionId) {
        InjectionRow row =
                ledger.findInjection(injectionId)
                        .orElseThrow(
                                () -> new NotFoundException("no such injection: " + injectionId));
        if (!ACTIVE.equals(row.state())) {
            // Aborting something already over is not an error: whoever asked wanted the fault gone,
            // and it is gone.
            return toInjection(row);
        }
        revert(row, ABORTED);
        return status(injectionId);
    }

    /** Expire injections that have outlived their declared maximum duration. */
    @Scheduled(fixedDelay = 1000L)
    public void expire() {
        OffsetDateTime now = OffsetDateTime.now(clock);
        for (InjectionRow row : ledger.injectionsInState(ACTIVE)) {
            if (row.expiresAt() != null && !row.expiresAt().isAfter(now)) {
                revert(row, COMPLETED);
                LOG.info("injection {} reached its declared maximum duration and was reverted",
                        row.injectionId());
            }
        }
    }

    private void revert(InjectionRow row, String endState) {
        Fault fault = faults.get(row.scenarioId());
        if (fault != null) {
            fault.revert(row.params());
        }
        ledger.endInjection(row.injectionId(), endState, OffsetDateTime.now(clock));
        logs.record(
                "info",
                "chaos",
                "chaos",
                "injection " + row.injectionId() + " " + endState + " and reverted",
                null,
                Map.of("scenario_id", row.scenarioId(), "injection_id", row.injectionId()));
    }

    static void assertDrillScope(String scope) {
        if (scope == null || !scope.startsWith(DRILL_SCOPE_PREFIX)) {
            throw new IllegalArgumentException(
                    "a chaos injection needs an authorised drill scope like drill:<id>, got: " + scope);
        }
    }

    private static Injection toInjection(InjectionRow row) {
        return new Injection(
                row.injectionId(),
                row.scenarioId(),
                row.target(),
                row.params(),
                row.state(),
                row.scope(),
                row.startedAt(),
                row.endedAt());
    }

    private String linkOf(Map<String, Object> params) {
        Object raw = params.get("link");
        if (raw == null) {
            throw new IllegalArgumentException("this scenario needs a link parameter");
        }
        return String.valueOf(raw);
    }

    private String regionOf(Map<String, Object> params) {
        Object raw = params.get("region");
        String region = raw == null ? null : String.valueOf(raw);
        if (region == null || !config.regions().contains(region)) {
            throw new IllegalArgumentException(
                    "region must be one of " + config.regions() + ", got " + raw);
        }
        return region;
    }

    private static double doubleOf(Object value, double fallback) {
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        if (value instanceof String text && !text.isBlank()) {
            try {
                return Double.parseDouble(text.trim());
            } catch (NumberFormatException ignored) {
                return fallback;
            }
        }
        return fallback;
    }
}
