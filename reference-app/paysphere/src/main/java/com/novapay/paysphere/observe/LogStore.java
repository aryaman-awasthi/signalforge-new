package com.novapay.paysphere.observe;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.novapay.paysphere.HarnessProperties;

import jakarta.annotation.PreDestroy;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * The application's own structured log: a JSONL file on disk and a bounded ring in memory.
 *
 * <p>{@code /observe/logs} reads the ring. That is a deliberate limitation and it is stated rather
 * than hidden: the observe contract asks an application to be able to list its logs over a window,
 * not to run a log warehouse, and a ring of a few thousand lines is what a single-process harness
 * honestly has. The file is the durable copy for anyone who wants the whole history.
 */
@Component
public class LogStore {

    /** Exactly the frozen LogRecord shape - no field the platform would refuse. */
    public record Entry(
            Instant ts,
            String level,
            String message,
            String service,
            String logger,
            String traceId,
            Map<String, String> attributes) {}

    private static final Logger LOG = LoggerFactory.getLogger(LogStore.class);

    private final Deque<Entry> ring = new ArrayDeque<>();
    private final int capacity;
    private final ObjectMapper json;
    private final BufferedWriter file;

    public LogStore(HarnessProperties properties, ObjectMapper json) {
        this.capacity = Math.max(100, properties.getLogRingSize());
        this.json = json;
        this.file = openFile(properties.getLogFile());
    }

    private BufferedWriter openFile(String configured) {
        try {
            Path path = Path.of(configured).toAbsolutePath();
            Files.createDirectories(path.getParent());
            return Files.newBufferedWriter(
                    path,
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.APPEND);
        } catch (IOException exc) {
            // A harness that cannot open its log file still has to serve telemetry; losing the
            // durable copy is worth reporting, not worth refusing to start over.
            LOG.warn("log file {} is not writable, keeping the in-memory ring only", configured, exc);
            return null;
        }
    }

    public void record(
            String level,
            String service,
            String logger,
            String message,
            String traceId,
            Map<String, String> attributes) {
        Entry entry =
                new Entry(
                        Instant.now(),
                        level,
                        message,
                        service,
                        logger,
                        traceId,
                        attributes == null ? Map.of() : Map.copyOf(attributes));
        synchronized (ring) {
            ring.addLast(entry);
            while (ring.size() > capacity) {
                ring.removeFirst();
            }
        }
        append(entry);
    }

    private void append(Entry entry) {
        if (file == null) {
            return;
        }
        try {
            synchronized (file) {
                file.write(json.writeValueAsString(entry));
                file.newLine();
                file.flush();
            }
        } catch (Exception exc) {
            LOG.debug("could not append to the log file", exc);
        }
    }

    /**
     * Substring match over message, service and level, bounded by the window and the limit. Matching
     * is case-insensitive and there is no query language, which is what the contract asks for: an
     * application has to be searchable, not queryable.
     */
    public List<Entry> search(String query, Instant start, Instant end, int limit) {
        String needle = query == null ? "" : query.trim().toLowerCase();
        List<Entry> snapshot;
        synchronized (ring) {
            snapshot = new ArrayList<>(ring);
        }
        List<Entry> matched = new ArrayList<>();
        for (Entry entry : snapshot) {
            if (start != null && entry.ts().isBefore(start)) {
                continue;
            }
            if (end != null && entry.ts().isAfter(end)) {
                continue;
            }
            if (!needle.isEmpty() && !searchable(entry).contains(needle)) {
                continue;
            }
            matched.add(entry);
        }
        if (limit > 0 && matched.size() > limit) {
            // The most recent records are the ones an incident is about.
            return new ArrayList<>(matched.subList(matched.size() - limit, matched.size()));
        }
        return matched;
    }

    public int total(String query, Instant start, Instant end) {
        return search(query, start, end, 0).size();
    }

    private static String searchable(Entry entry) {
        StringBuilder text = new StringBuilder();
        text.append(entry.message()).append(' ').append(entry.level());
        if (entry.service() != null) {
            text.append(' ').append(entry.service());
        }
        if (entry.logger() != null) {
            text.append(' ').append(entry.logger());
        }
        for (Map.Entry<String, String> attribute : entry.attributes().entrySet()) {
            text.append(' ').append(attribute.getKey()).append('=').append(attribute.getValue());
        }
        return text.toString().toLowerCase();
    }

    @PreDestroy
    void close() {
        if (file == null) {
            return;
        }
        try {
            synchronized (file) {
                file.close();
            }
        } catch (IOException ignored) {
            // Shutting down; there is nowhere useful left to report this.
        }
    }
}
