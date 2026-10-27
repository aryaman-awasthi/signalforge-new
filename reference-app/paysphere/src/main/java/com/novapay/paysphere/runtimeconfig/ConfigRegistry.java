package com.novapay.paysphere.runtimeconfig;

import java.time.Clock;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

import com.novapay.paysphere.HarnessProperties;
import com.novapay.paysphere.ledger.ConfigVersionRow;
import com.novapay.paysphere.ledger.Ledger;

import jakarta.annotation.PostConstruct;

import org.springframework.stereotype.Component;

/**
 * The per-region runtime configuration a deployment changes and a rollback undoes.
 *
 * <p>Versions are append-only and every version is persisted, which is what makes
 * {@code rollback-config} a real action rather than a gesture: it re-applies a version that
 * genuinely existed, and refuses a version number that never did.
 *
 * <p>{@code applyDelayMs} is here because it is the honest place for it. The chaos scenario
 * {@code rollback-delay} sets it, and the effect is that an apply takes longer than the caller is
 * willing to wait. The caller times out; the harness finishes anyway. That is not a bug being
 * simulated - it is the ordinary shape of a distributed change, and it is the only way to produce
 * an UNCERTAIN execution that is genuinely uncertain rather than merely labelled so.
 */
@Component
public class ConfigRegistry {

    public record RegionConfig(
            String region, int version, int timeoutMs, int poolSize, OffsetDateTime appliedAt) {

        public Map<String, Object> settings() {
            Map<String, Object> settings = new LinkedHashMap<>();
            settings.put("timeout_ms", timeoutMs);
            settings.put("pool_size", poolSize);
            return settings;
        }
    }

    private static final int DEFAULT_TIMEOUT_MS = 2000;
    private static final int DEFAULT_POOL_SIZE = 16;

    private final Ledger ledger;
    private final HarnessProperties properties;
    private final Clock clock;
    private final Map<String, RegionConfig> current = new ConcurrentHashMap<>();
    private final AtomicLong applyDelayMs = new AtomicLong(0L);

    public ConfigRegistry(Ledger ledger, HarnessProperties properties, Clock clock) {
        this.ledger = ledger;
        this.properties = properties;
        this.clock = clock;
    }

    @PostConstruct
    void seedFromLedger() {
        for (String region : properties.getRegions()) {
            List<ConfigVersionRow> history = ledger.configHistory(region);
            if (history.isEmpty()) {
                RegionConfig initial =
                        new RegionConfig(
                                region,
                                1,
                                DEFAULT_TIMEOUT_MS,
                                DEFAULT_POOL_SIZE,
                                OffsetDateTime.now(clock));
                current.put(region, initial);
                ledger.saveConfigVersion(
                        region, 1, initial.settings(), "bootstrap", initial.appliedAt());
            } else {
                ConfigVersionRow latest = history.get(history.size() - 1);
                current.put(region, fromRow(latest));
            }
        }
    }

    public List<String> regions() {
        return List.copyOf(properties.getRegions());
    }

    public RegionConfig get(String region) {
        RegionConfig config = current.get(region);
        if (config == null) {
            throw new IllegalArgumentException("no such region: " + region);
        }
        return config;
    }

    public List<ConfigVersionRow> history(String region) {
        return ledger.configHistory(region);
    }

    /**
     * Apply new settings to a region, honouring whatever delay chaos has injected. Returns the new
     * version. The delay is taken before the change lands, so a caller that gives up mid-apply has
     * genuinely not learned whether it landed.
     */
    public RegionConfig apply(String region, Map<String, Object> settings, String source) {
        RegionConfig existing = get(region);
        long delay = applyDelayMs.get();
        if (delay > 0) {
            sleep(delay);
        }
        int timeoutMs = intOr(settings.get("timeout_ms"), existing.timeoutMs());
        int poolSize = intOr(settings.get("pool_size"), existing.poolSize());
        RegionConfig applied =
                new RegionConfig(
                        region,
                        existing.version() + 1,
                        timeoutMs,
                        poolSize,
                        OffsetDateTime.now(clock));
        ledger.saveConfigVersion(
                region, applied.version(), applied.settings(), source, applied.appliedAt());
        current.put(region, applied);
        return applied;
    }

    /** Re-apply an earlier version. Throws if that version never existed. */
    public RegionConfig rollbackTo(String region, int targetVersion) {
        ConfigVersionRow target =
                ledger.findConfigVersion(region, targetVersion)
                        .orElseThrow(
                                () ->
                                        new IllegalArgumentException(
                                                "region "
                                                        + region
                                                        + " has no configuration version "
                                                        + targetVersion));
        return apply(region, target.settings(), "rollback-to-v" + targetVersion);
    }

    public void setApplyDelayMs(long delayMs) {
        applyDelayMs.set(Math.max(0L, delayMs));
    }

    public long applyDelayMs() {
        return applyDelayMs.get();
    }

    private static RegionConfig fromRow(ConfigVersionRow row) {
        return new RegionConfig(
                row.region(),
                row.version(),
                intOr(row.settings().get("timeout_ms"), DEFAULT_TIMEOUT_MS),
                intOr(row.settings().get("pool_size"), DEFAULT_POOL_SIZE),
                row.appliedAt());
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

    private static void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }
}
