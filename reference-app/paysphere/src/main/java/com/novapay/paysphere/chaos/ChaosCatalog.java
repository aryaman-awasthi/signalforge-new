package com.novapay.paysphere.chaos;

import java.io.InputStream;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.novapay.paysphere.NotFoundException;

import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;

/**
 * The published chaos manifest. Like the action manifest, it is a written document rather than a
 * scan of the code, and {@link ChaosEngine} refuses to start if a scenario has no implementation.
 *
 * <p>Publishing what an application can be made to do is not the same as authorising it: every
 * injection additionally needs a live drill scope, checked at the door.
 */
@Component
public class ChaosCatalog {

    private final Map<String, Object> document;
    private final List<String> scenarioIds;

    public ChaosCatalog(ObjectMapper json) {
        this.document = load(json);
        List<String> ids = new ArrayList<>();
        for (Map<String, Object> scenario : scenarios()) {
            ids.add(String.valueOf(scenario.get("scenario_id")));
        }
        this.scenarioIds = List.copyOf(ids);
    }

    private static Map<String, Object> load(ObjectMapper json) {
        try (InputStream stream = new ClassPathResource("observe/chaos-manifest.json").getInputStream()) {
            return json.readValue(stream, new TypeReference<Map<String, Object>>() {});
        } catch (Exception exc) {
            throw new IllegalStateException("the chaos manifest could not be read", exc);
        }
    }

    public Map<String, Object> document() {
        return document;
    }

    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> scenarios() {
        Object raw = document.get("scenarios");
        return raw instanceof List<?> list ? (List<Map<String, Object>>) list : List.of();
    }

    public Set<String> scenarioIdSet() {
        return new LinkedHashSet<>(scenarioIds);
    }

    public Map<String, Object> scenario(String scenarioId) {
        for (Map<String, Object> scenario : scenarios()) {
            if (scenarioId.equals(scenario.get("scenario_id"))) {
                return scenario;
            }
        }
        throw new NotFoundException(
                "this application publishes no chaos scenario called " + scenarioId);
    }

    public double maxDurationS(String scenarioId) {
        Object raw = scenario(scenarioId).get("max_duration_s");
        return raw instanceof Number number ? number.doubleValue() : 300.0;
    }

    public String target(String scenarioId) {
        return String.valueOf(scenario(scenarioId).get("target"));
    }
}
