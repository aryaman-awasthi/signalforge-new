package com.novapay.paysphere.runtimeconfig;

import java.util.LinkedHashMap;
import java.util.Map;

import com.novapay.paysphere.observe.LogStore;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * The application's own deployment API, used by the config repository's {@code deploy.sh}.
 *
 * <p>It is deliberately not part of the observe contract and not reachable with any observability
 * credential. SignalForge learns that a deployment happened because the pipeline <em>tells</em> it,
 * over the public ingest contract - not because the platform was given a way to deploy. Keeping
 * those two directions apart is most of what makes "wired only via public contracts" true.
 */
@RestController
public class DeployController {

    public record DeployRequest(String release, Map<String, Object> settings) {}

    private final ConfigRegistry config;
    private final LogStore logs;

    public DeployController(ConfigRegistry config, LogStore logs) {
        this.config = config;
        this.logs = logs;
    }

    @PostMapping("/api/deploy/{region}")
    public Map<String, Object> deploy(
            @PathVariable("region") String region, @RequestBody DeployRequest request) {
        if (!config.regions().contains(region)) {
            throw new IllegalArgumentException(
                    "region must be one of " + config.regions() + ", got " + region);
        }
        String release = request == null || request.release() == null ? "unversioned" : request.release();
        Map<String, Object> settings =
                request == null || request.settings() == null ? Map.of() : request.settings();
        int before = config.get(region).version();

        // Re-running a pipeline is normal - a retried job, a re-triggered stage, an operator making
        // sure. Applying the same release with the same settings twice is therefore one change, so
        // the harness's version history matches what the platform recorded from the ingest event
        // rather than growing a version nobody made.
        var history = config.history(region);
        if (!history.isEmpty()) {
            var latest = history.get(history.size() - 1);
            if (("deploy:" + release).equals(latest.source())
                    && latest.settings().equals(normalise(region, settings))) {
                Map<String, Object> unchanged = new LinkedHashMap<>();
                unchanged.put("region", region);
                unchanged.put("release", release);
                unchanged.put("previous_version", before);
                unchanged.put("version", before);
                unchanged.put("settings", latest.settings());
                unchanged.put("applied_at", latest.appliedAt().toString());
                unchanged.put("changed", false);
                return unchanged;
            }
        }

        ConfigRegistry.RegionConfig applied = config.apply(region, settings, "deploy:" + release);
        logs.record(
                "info",
                "config-registry",
                "deploy",
                "release " + release + " applied to " + region,
                null,
                Map.of("region", region, "release", release,
                        "config_version", Integer.toString(applied.version())));
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("region", region);
        body.put("release", release);
        body.put("previous_version", before);
        body.put("version", applied.version());
        body.put("settings", applied.settings());
        body.put("applied_at", applied.appliedAt().toString());
        body.put("changed", true);
        return body;
    }

    /**
     * The stored settings are the two the registry understands, in its own order and types, so a
     * pipeline that sends them as strings or in a different order still compares equal.
     */
    private Map<String, Object> normalise(String region, Map<String, Object> settings) {
        Map<String, Object> merged = new LinkedHashMap<>(config.get(region).settings());
        merged.putAll(settings);
        Map<String, Object> normalised = new LinkedHashMap<>();
        normalised.put("timeout_ms", asInt(merged.get("timeout_ms")));
        normalised.put("pool_size", asInt(merged.get("pool_size")));
        return normalised;
    }

    private static Object asInt(Object value) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        if (value instanceof String text && !text.isBlank()) {
            try {
                return Integer.parseInt(text.trim());
            } catch (NumberFormatException ignored) {
                return value;
            }
        }
        return value;
    }

    @GetMapping("/api/deploy/{region}")
    public Map<String, Object> current(@PathVariable("region") String region) {
        if (!config.regions().contains(region)) {
            throw new IllegalArgumentException(
                    "region must be one of " + config.regions() + ", got " + region);
        }
        ConfigRegistry.RegionConfig current = config.get(region);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("region", region);
        body.put("version", current.version());
        body.put("settings", current.settings());
        body.put("applied_at", current.appliedAt().toString());
        return body;
    }
}
