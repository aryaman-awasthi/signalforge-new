package com.novapay.paysphere.observe;

import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Map;

import com.novapay.paysphere.HarnessProperties;

import org.springframework.stereotype.Component;

/**
 * A bounded in-memory span buffer, which is what a single-process harness can honestly offer for
 * {@code /observe/traces}. Spans carry the same trace id as the log lines and the payment row, so a
 * trace, its logs and its ledger entry can be joined by anyone holding one of the three.
 */
@Component
public class TraceStore {

    /** Exactly the frozen TraceSpan shape. */
    public record Span(
            String traceId,
            String spanId,
            String parentSpanId,
            String service,
            String operation,
            Instant start,
            Instant end,
            String status,
            Map<String, String> attributes) {}

    public static final String OK = "ok";
    public static final String ERROR = "error";

    private final Deque<Span> ring = new ArrayDeque<>();
    private final int capacity;

    public TraceStore(HarnessProperties properties) {
        this.capacity = Math.max(100, properties.getTraceRingSize());
    }

    public void record(Span span) {
        synchronized (ring) {
            ring.addLast(span);
            while (ring.size() > capacity) {
                ring.removeFirst();
            }
        }
    }

    public List<Span> byTrace(String traceId) {
        List<Span> matched = new ArrayList<>();
        for (Span span : snapshot()) {
            if (span.traceId().equals(traceId)) {
                matched.add(span);
            }
        }
        return matched;
    }

    public List<Span> search(
            Instant start,
            Instant end,
            String service,
            String operation,
            boolean errorsOnly,
            int limit) {
        List<Span> matched = new ArrayList<>();
        for (Span span : snapshot()) {
            if (start != null && span.end().isBefore(start)) {
                continue;
            }
            if (end != null && span.start().isAfter(end)) {
                continue;
            }
            if (service != null && !service.isBlank() && !service.equals(span.service())) {
                continue;
            }
            if (operation != null && !operation.isBlank() && !operation.equals(span.operation())) {
                continue;
            }
            if (errorsOnly && !ERROR.equals(span.status())) {
                continue;
            }
            matched.add(span);
        }
        if (limit > 0 && matched.size() > limit) {
            return new ArrayList<>(matched.subList(matched.size() - limit, matched.size()));
        }
        return matched;
    }

    private List<Span> snapshot() {
        synchronized (ring) {
            return new ArrayList<>(ring);
        }
    }
}
