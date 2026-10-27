package com.novapay.paysphere.observe;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import com.novapay.paysphere.fabric.FabricSimulator;
import com.novapay.paysphere.runtimeconfig.ConfigRegistry;

import org.springframework.stereotype.Component;

/**
 * {@code /observe/topology}: what this application is made of and what it currently depends on.
 *
 * <p>The edges are derived from the <em>live</em> routing table, not from a drawing, so failing a
 * region over changes the graph the platform holds. That is the point of publishing topology at all:
 * a blast radius computed from a map that was true last quarter is a guess.
 *
 * <p>The version is a content fingerprint of the nodes and edges, deliberately excluding the capture
 * time. Re-reading an unchanged topology therefore yields the same version, and the platform stores
 * one version rather than one per poll.
 */
@Component
public class TopologyService {

    public record Node(
            String nodeId, String name, String nodeType, String region, Map<String, String> labels) {}

    public record Edge(String source, String target, String edgeType, Map<String, String> labels) {}

    public record Snapshot(String version, Instant capturedAt, List<Node> nodes, List<Edge> edges) {}

    private static final Map<String, String> LINK_NODES =
            Map.of(
                    FabricSimulator.GW_A, "gateway-a",
                    FabricSimulator.GW_B, "gateway-b",
                    FabricSimulator.AGG_A, "aggregator-a",
                    FabricSimulator.AGG_B, "aggregator-b",
                    FabricSimulator.FIB_7, "fibre-7",
                    FabricSimulator.CORE, "core-switch",
                    FabricSimulator.RAIL, "settlement-rail");

    private final FabricSimulator fabric;
    private final ConfigRegistry config;
    private final Clock clock;

    public TopologyService(FabricSimulator fabric, ConfigRegistry config, Clock clock) {
        this.fabric = fabric;
        this.config = config;
        this.clock = clock;
    }

    public Snapshot snapshot() {
        List<Node> nodes = nodes();
        List<Edge> edges = edges();
        return new Snapshot(fingerprint(nodes, edges), Instant.now(clock), nodes, edges);
    }

    private List<Node> nodes() {
        List<Node> nodes = new ArrayList<>();
        nodes.add(node("gateway-a", "Payment gateway (region-a)", "service", "region-a", "GW-A"));
        nodes.add(node("gateway-b", "Payment gateway (region-b)", "service", "region-b", "GW-B"));
        nodes.add(node("aggregator-a", "Payment aggregator (region-a)", "service", "region-a", "AGG-A"));
        nodes.add(node("aggregator-b", "Payment aggregator (region-b)", "service", "region-b", "AGG-B"));
        nodes.add(node("fibre-7", "Shared fibre span FIB-7", "other", null, "FIB-7"));
        nodes.add(node("core-switch", "Core switch", "other", null, "CORE"));
        nodes.add(node("settlement-rail", "Settlement rail", "external", null, "RAIL"));
        nodes.add(new Node("ledger-db", "Payment ledger", "database", null, Map.of("engine", "h2")));
        nodes.add(
                new Node(
                        "config-registry",
                        "Runtime config registry",
                        "service",
                        null,
                        Map.of("versions", Integer.toString(config.regions().size()))));
        return nodes;
    }

    private static Node node(String id, String name, String type, String region, String link) {
        return new Node(id, name, type, region, Map.of("fabric_link", link));
    }

    private List<Edge> edges() {
        Set<String> seen = new LinkedHashSet<>();
        List<Edge> edges = new ArrayList<>();
        for (Map.Entry<String, List<String>> entry : fabric.routingTable().entrySet()) {
            List<String> route = entry.getValue();
            for (int i = 0; i + 1 < route.size(); i++) {
                String source = LINK_NODES.get(route.get(i));
                String target = LINK_NODES.get(route.get(i + 1));
                if (seen.add(source + "->" + target)) {
                    edges.add(new Edge(source, target, "calls", Map.of("region", entry.getKey())));
                }
            }
        }
        for (String gateway : List.of("gateway-a", "gateway-b")) {
            edges.add(new Edge(gateway, "ledger-db", "depends_on", Map.of()));
            edges.add(new Edge(gateway, "config-registry", "depends_on", Map.of()));
        }
        return edges;
    }

    /**
     * A stable digest of the graph itself. Node order and capture time are excluded, so a poll that
     * finds nothing changed produces the version it produced last time.
     */
    private static String fingerprint(List<Node> nodes, List<Edge> edges) {
        List<String> lines = new ArrayList<>();
        for (Node node : nodes) {
            lines.add(
                    "n|"
                            + node.nodeId()
                            + "|"
                            + node.nodeType()
                            + "|"
                            + (node.region() == null ? "" : node.region())
                            + "|"
                            + new LinkedHashMap<>(new java.util.TreeMap<>(node.labels())));
        }
        for (Edge edge : edges) {
            lines.add(
                    "e|"
                            + edge.source()
                            + "|"
                            + edge.target()
                            + "|"
                            + edge.edgeType()
                            + "|"
                            + new LinkedHashMap<>(new java.util.TreeMap<>(edge.labels())));
        }
        java.util.Collections.sort(lines);
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(String.join("\n", lines).getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (int i = 0; i < 8; i++) {
                hex.append(String.format("%02x", hash[i]));
            }
            return "sha256:" + hex;
        } catch (Exception exc) {
            throw new IllegalStateException("SHA-256 is required to version a topology", exc);
        }
    }
}
