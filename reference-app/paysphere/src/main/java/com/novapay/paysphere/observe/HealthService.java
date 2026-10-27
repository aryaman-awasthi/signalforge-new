package com.novapay.paysphere.observe;

import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

import com.novapay.paysphere.fabric.FabricSimulator;
import com.novapay.paysphere.ledger.Ledger;
import com.novapay.paysphere.ledger.PaymentState;
import com.novapay.paysphere.payments.ProcessorLicense;
import com.novapay.paysphere.runtimeconfig.ConfigRegistry;

import org.springframework.stereotype.Component;

/**
 * {@code /observe/health}: the application's own opinion of itself, per component and per region.
 *
 * <p>The regional components are what make this worth reading. A platform that only learns "the app
 * is degraded" has to go and find out where; one that learns "region-b's fabric is degraded and
 * region-a's is up" already has half a blast radius. The report is computed from the same fabric and
 * ledger state the metrics are computed from, so health and metrics cannot contradict each other.
 */
@Component
public class HealthService {

    /** Exactly the frozen ComponentHealth and HealthReport shapes. */
    public record ComponentStatus(String component, String status, String detail) {}

    public record Report(String status, List<ComponentStatus> components, Instant checkedAt) {}

    public static final String UP = "up";
    public static final String DEGRADED = "degraded";
    public static final String DOWN = "down";

    private final FabricSimulator fabric;
    private final ConfigRegistry config;
    private final Ledger ledger;
    private final ProcessorLicense license;
    private final Clock clock;

    public HealthService(
            FabricSimulator fabric,
            ConfigRegistry config,
            Ledger ledger,
            ProcessorLicense license,
            Clock clock) {
        this.fabric = fabric;
        this.config = config;
        this.ledger = ledger;
        this.license = license;
        this.clock = clock;
    }

    public Report report() {
        List<ComponentStatus> components = new ArrayList<>();
        components.add(ledgerHealth());
        components.add(licenseHealth());
        for (String region : config.regions()) {
            components.add(regionHealth(region));
        }
        components.add(railHealth());
        components.add(configHealth());
        return new Report(worst(components), components, Instant.now(clock));
    }

    private ComponentStatus ledgerHealth() {
        try {
            long unknown = 0;
            for (String region : config.regions()) {
                unknown += ledger.countPayments(region, PaymentState.UNKNOWN);
            }
            String detail = unknown == 0 ? null : unknown + " payments awaiting reconciliation";
            return new ComponentStatus("ledger", unknown > 50 ? DEGRADED : UP, detail);
        } catch (RuntimeException exc) {
            return new ComponentStatus("ledger", DOWN, exc.getClass().getSimpleName() + ": " + exc.getMessage());
        }
    }

    /**
     * The one component whose failure has nothing to do with the fabric. The detail is the
     * processor's own refusal sentence, because the platform reads component details as evidence and
     * "down" alone would send it looking for a link that is not broken.
     */
    private ComponentStatus licenseHealth() {
        if (license.expired()) {
            return new ComponentStatus(ProcessorLicense.COMPONENT, DOWN, license.refusalDetail());
        }
        long days = license.secondsRemaining() / 86_400L;
        return new ComponentStatus(
                ProcessorLicense.COMPONENT, UP, "valid until " + license.validUntil() + " (" + days + " days)");
    }

    private ComponentStatus regionHealth(String region) {
        List<String> route = fabric.route(region);
        List<String> down = new ArrayList<>();
        List<String> lossy = new ArrayList<>();
        for (String linkId : route) {
            FabricSimulator.Link link = fabric.link(linkId);
            if (!link.up()) {
                down.add(linkId);
            } else if (link.lossRatio() > 0 || link.injectedLatencyMs() > 0) {
                lossy.add(linkId);
            }
        }
        String name = "fabric-" + region;
        if (!down.isEmpty()) {
            return new ComponentStatus(name, DOWN, "link(s) down on the active route: " + String.join(", ", down));
        }
        if (!lossy.isEmpty()) {
            return new ComponentStatus(
                    name, DEGRADED, "link(s) impaired on the active route: " + String.join(", ", lossy));
        }
        return new ComponentStatus(name, UP, fabric.activeRoute(region) + " route: " + String.join(">", route));
    }

    private ComponentStatus railHealth() {
        FabricSimulator.Link rail = fabric.link(FabricSimulator.RAIL);
        if (!rail.up()) {
            return new ComponentStatus("settlement-rail", DOWN, "the settlement rail is not answering");
        }
        if (rail.lossRatio() > 0) {
            return new ComponentStatus(
                    "settlement-rail",
                    DEGRADED,
                    "dropping " + Math.round(rail.lossRatio() * 100) + "% of settlements");
        }
        if (rail.injectedLatencyMs() > 0) {
            return new ComponentStatus(
                    "settlement-rail",
                    DEGRADED,
                    "+" + Math.round(rail.injectedLatencyMs()) + "ms added latency");
        }
        return new ComponentStatus("settlement-rail", UP, null);
    }

    private ComponentStatus configHealth() {
        long delay = config.applyDelayMs();
        if (delay > 0) {
            return new ComponentStatus(
                    "config-registry", DEGRADED, "applies are taking an extra " + delay + "ms");
        }
        return new ComponentStatus("config-registry", UP, null);
    }

    private static String worst(List<ComponentStatus> components) {
        String worst = UP;
        for (ComponentStatus component : components) {
            if (DOWN.equals(component.status())) {
                return DOWN;
            }
            if (DEGRADED.equals(component.status())) {
                worst = DEGRADED;
            }
        }
        return worst;
    }
}
