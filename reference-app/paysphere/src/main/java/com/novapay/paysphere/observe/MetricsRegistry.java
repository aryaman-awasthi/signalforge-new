package com.novapay.paysphere.observe;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.DoubleAdder;

import org.springframework.stereotype.Component;

/**
 * A small Prometheus text-exposition registry.
 *
 * <p>Written by hand rather than taken from a metrics library for one reason: the platform's parser
 * refuses a malformed exposition line instead of dropping it, so the harness has to control its
 * output format exactly rather than trust whatever a library's defaults produce this month. Every
 * metric declares its label names up front and a sample with a different label set is rejected, so
 * a series cannot silently split in two and make a counter look like it reset.
 */
@Component
public class MetricsRegistry {

    private enum Kind {
        COUNTER,
        GAUGE
    }

    private record Definition(String name, Kind kind, String help, java.util.List<String> labels) {}

    private record Series(String name, Map<String, String> labels) implements Comparable<Series> {

        @Override
        public int compareTo(Series other) {
            int byName = name.compareTo(other.name);
            return byName != 0 ? byName : labels.toString().compareTo(other.labels.toString());
        }
    }

    private final Map<String, Definition> definitions = new ConcurrentHashMap<>();
    private final Map<Series, DoubleAdder> counters = new ConcurrentHashMap<>();
    private final Map<Series, Double> gauges = new ConcurrentHashMap<>();

    public void declareCounter(String name, String help, String... labels) {
        definitions.put(name, new Definition(name, Kind.COUNTER, help, java.util.List.of(labels)));
    }

    public void declareGauge(String name, String help, String... labels) {
        definitions.put(name, new Definition(name, Kind.GAUGE, help, java.util.List.of(labels)));
    }

    public void increment(String name, Map<String, String> labels, double amount) {
        counters.computeIfAbsent(series(name, Kind.COUNTER, labels), key -> new DoubleAdder())
                .add(amount);
    }

    public void setGauge(String name, Map<String, String> labels, double value) {
        gauges.put(series(name, Kind.GAUGE, labels), value);
    }

    /**
     * Registering a zero-valued counter so the series exists before anything happens. A counter that
     * only appears once it is non-zero makes "errors went from nothing to something" indistinguishable
     * from "the exporter restarted", which is exactly the confusion detection cannot afford.
     */
    public void preregister(String name, Map<String, String> labels) {
        counters.computeIfAbsent(series(name, Kind.COUNTER, labels), key -> new DoubleAdder());
    }

    private Series series(String name, Kind kind, Map<String, String> labels) {
        Definition definition = definitions.get(name);
        if (definition == null) {
            throw new IllegalArgumentException("metric " + name + " was never declared");
        }
        if (definition.kind() != kind) {
            throw new IllegalArgumentException("metric " + name + " is a " + definition.kind());
        }
        if (!definition.labels().containsAll(labels.keySet())
                || labels.size() != definition.labels().size()) {
            throw new IllegalArgumentException(
                    "metric "
                            + name
                            + " declares labels "
                            + definition.labels()
                            + " but was given "
                            + labels.keySet());
        }
        return new Series(name, new TreeMap<>(labels));
    }

    /** The whole exposition, grouped by metric with its HELP and TYPE lines. */
    public String scrape() {
        Map<String, StringBuilder> byMetric = new LinkedHashMap<>();
        for (String name : definitions.keySet().stream().sorted().toList()) {
            Definition definition = definitions.get(name);
            StringBuilder block = new StringBuilder();
            block.append("# HELP ").append(name).append(' ').append(escapeHelp(definition.help()))
                    .append('\n');
            block.append("# TYPE ")
                    .append(name)
                    .append(' ')
                    .append(definition.kind().name().toLowerCase())
                    .append('\n');
            byMetric.put(name, block);
        }
        counters.entrySet().stream()
                .sorted(Map.Entry.comparingByKey())
                .forEach(
                        entry ->
                                append(
                                        byMetric,
                                        entry.getKey(),
                                        entry.getValue().sum()));
        gauges.entrySet().stream()
                .sorted(Map.Entry.comparingByKey())
                .forEach(entry -> append(byMetric, entry.getKey(), entry.getValue()));

        StringBuilder text = new StringBuilder();
        for (StringBuilder block : byMetric.values()) {
            text.append(block);
        }
        return text.toString();
    }

    private void append(Map<String, StringBuilder> byMetric, Series series, double value) {
        StringBuilder block = byMetric.get(series.name());
        if (block == null) {
            return;
        }
        block.append(series.name());
        if (!series.labels().isEmpty()) {
            block.append('{');
            boolean first = true;
            for (Map.Entry<String, String> label : series.labels().entrySet()) {
                if (!first) {
                    block.append(',');
                }
                first = false;
                block.append(label.getKey())
                        .append("=\"")
                        .append(escapeLabel(label.getValue()))
                        .append('"');
            }
            block.append('}');
        }
        block.append(' ').append(format(value)).append('\n');
    }

    static String format(double value) {
        if (Double.isNaN(value)) {
            return "NaN";
        }
        if (Double.isInfinite(value)) {
            return value > 0 ? "+Inf" : "-Inf";
        }
        if (value == Math.rint(value) && Math.abs(value) < 1e15) {
            return Long.toString((long) value);
        }
        return String.format(java.util.Locale.ROOT, "%.6f", value);
    }

    static String escapeLabel(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n");
    }

    static String escapeHelp(String value) {
        return value.replace("\\", "\\\\").replace("\n", "\\n");
    }
}
