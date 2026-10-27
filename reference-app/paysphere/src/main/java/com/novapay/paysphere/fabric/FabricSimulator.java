package com.novapay.paysphere.fabric;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

import org.springframework.stereotype.Component;

/**
 * The payment fabric: seven links, one routing table per region, and a way to hurt either.
 *
 * <p>The harness needs a topology that is real enough to reason about, because half of what the
 * platform has to demonstrate - blast radius, corroboration, "why is only one region affected" - is
 * meaningless if every failure is global. So the two regions share {@code CORE} and {@code RAIL}
 * and nothing else on their primary routes, except that region-b's primary route crosses the shared
 * fibre span {@code FIB-7}. Failing region-b over moves it off FIB-7; nothing moves either region
 * off the rail, because the rail is the one dependency a payments business cannot route around.
 *
 * <p>A failure at the rail is reported as <em>uncertain</em>, not failed. That is the whole reason
 * the ledger has an UNKNOWN state: the request reached the money movement layer and the answer was
 * lost, so claiming failure would be a guess and retrying would be a second payment.
 */
@Component
public class FabricSimulator {

    public static final String GW_A = "GW-A";
    public static final String GW_B = "GW-B";
    public static final String AGG_A = "AGG-A";
    public static final String AGG_B = "AGG-B";
    public static final String FIB_7 = "FIB-7";
    public static final String CORE = "CORE";
    public static final String RAIL = "RAIL";

    /** Declared once, in the order a payment crosses them. */
    public static final List<String> LINK_IDS =
            List.of(GW_A, GW_B, AGG_A, AGG_B, FIB_7, CORE, RAIL);

    public static final String ROUTE_PRIMARY = "primary";
    public static final String ROUTE_SECONDARY = "secondary";

    private static final Map<String, Double> BASE_LATENCY_MS =
            Map.of(GW_A, 6.0, GW_B, 6.0, AGG_A, 9.0, AGG_B, 9.0, FIB_7, 14.0, CORE, 5.0, RAIL, 38.0);

    private final Map<String, Link> links = new ConcurrentHashMap<>();
    private final Map<String, String> activeRoute = new ConcurrentHashMap<>();
    private final AtomicInteger routingVersion = new AtomicInteger(1);

    public FabricSimulator() {
        for (String id : LINK_IDS) {
            links.put(id, new Link(id, BASE_LATENCY_MS.get(id)));
        }
        activeRoute.put("region-a", ROUTE_PRIMARY);
        activeRoute.put("region-b", ROUTE_PRIMARY);
    }

    /** One link's live state. Mutable on purpose: chaos and actions both move it. */
    public static final class Link {

        private final String id;
        private final double baseLatencyMs;
        private volatile boolean up = true;
        private volatile double lossRatio = 0.0;
        private volatile double injectedLatencyMs = 0.0;

        Link(String id, double baseLatencyMs) {
            this.id = id;
            this.baseLatencyMs = baseLatencyMs;
        }

        public String id() {
            return id;
        }

        public boolean up() {
            return up;
        }

        public double lossRatio() {
            return lossRatio;
        }

        public double latencyMs() {
            return baseLatencyMs + injectedLatencyMs;
        }

        public double injectedLatencyMs() {
            return injectedLatencyMs;
        }
    }

    /** What happened to one payment on its way through the fabric. */
    public record Transit(
            boolean ok, boolean uncertain, double latencyMs, String failedLink, List<String> path) {

        public boolean failed() {
            return !ok && !uncertain;
        }
    }

    public List<Link> links() {
        List<Link> ordered = new ArrayList<>();
        for (String id : LINK_IDS) {
            ordered.add(links.get(id));
        }
        return ordered;
    }

    public Link link(String id) {
        Link link = links.get(id);
        if (link == null) {
            throw new IllegalArgumentException("no such fabric link: " + id);
        }
        return link;
    }

    public int routingVersion() {
        return routingVersion.get();
    }

    public String activeRoute(String region) {
        return activeRoute.getOrDefault(region, ROUTE_PRIMARY);
    }

    /** The link ids a payment in this region currently crosses, in order. */
    public List<String> route(String region) {
        boolean secondary = ROUTE_SECONDARY.equals(activeRoute(region));
        if ("region-b".equals(region)) {
            return secondary
                    ? List.of(GW_B, AGG_A, CORE, RAIL)
                    : List.of(GW_B, AGG_B, FIB_7, CORE, RAIL);
        }
        return secondary ? List.of(GW_A, AGG_B, CORE, RAIL) : List.of(GW_A, AGG_A, CORE, RAIL);
    }

    /** All routes, for the topology snapshot: every region, both of its routes. */
    public Map<String, List<String>> routingTable() {
        Map<String, List<String>> table = new LinkedHashMap<>();
        for (String region : activeRoute.keySet().stream().sorted().toList()) {
            table.put(region, route(region));
        }
        return table;
    }

    /** Returns true when the route actually moved, so a repeat is visibly a no-op. */
    public boolean setRoute(String region, String route) {
        if (!ROUTE_PRIMARY.equals(route) && !ROUTE_SECONDARY.equals(route)) {
            throw new IllegalArgumentException("route must be primary or secondary, got " + route);
        }
        String previous = activeRoute.put(region, route);
        boolean changed = !route.equals(previous);
        if (changed) {
            routingVersion.incrementAndGet();
        }
        return changed;
    }

    public void setUp(String linkId, boolean up) {
        link(linkId).up = up;
    }

    public void setLoss(String linkId, double ratio) {
        link(linkId).lossRatio = Math.max(0.0, Math.min(1.0, ratio));
    }

    public void setInjectedLatency(String linkId, double latencyMs) {
        link(linkId).injectedLatencyMs = Math.max(0.0, latencyMs);
    }

    /**
     * Walk the region's route. A down or lossy link stops the payment there; if that link is the
     * rail the outcome is uncertain rather than failed, because the request had already reached the
     * point where money moves.
     */
    public Transit traverse(String region, Random random) {
        List<String> path = route(region);
        double latency = 0.0;
        for (String id : path) {
            Link link = links.get(id);
            latency += link.latencyMs();
            boolean lost = !link.up || random.nextDouble() < link.lossRatio();
            if (lost) {
                boolean uncertain = RAIL.equals(id);
                return new Transit(false, uncertain, latency, id, path);
            }
        }
        return new Transit(true, false, latency, null, path);
    }
}
