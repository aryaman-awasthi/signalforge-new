package com.novapay.paysphere.actions;

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
 * The published action manifest, loaded once from {@code observe/actions-manifest.json}.
 *
 * <p>The manifest is the declaration and the code is the implementation, and the harness refuses to
 * start if the two disagree - see {@link ActionExecutor}. That is stricter than generating the
 * manifest from the code by annotation scan, and deliberately so: a generated manifest cannot be
 * wrong, which means it also cannot be reviewed. Here the manifest is a document a human wrote and
 * a human can read, and drift is a startup failure rather than a surprise during an incident.
 */
@Component
public class ActionCatalog {

    private final Map<String, Object> document;
    private final List<String> actionIds;

    public ActionCatalog(ObjectMapper json) {
        this.document = load(json);
        List<String> ids = new ArrayList<>();
        for (Map<String, Object> action : actions()) {
            ids.add(String.valueOf(action.get("action_id")));
        }
        this.actionIds = List.copyOf(ids);
    }

    private static Map<String, Object> load(ObjectMapper json) {
        try (InputStream stream = new ClassPathResource("observe/actions-manifest.json").getInputStream()) {
            return json.readValue(stream, new TypeReference<Map<String, Object>>() {});
        } catch (Exception exc) {
            throw new IllegalStateException("the action manifest could not be read", exc);
        }
    }

    /** The manifest document exactly as published; the platform validates it against the schema. */
    public Map<String, Object> document() {
        return document;
    }

    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> actions() {
        Object raw = document.get("actions");
        return raw instanceof List<?> list ? (List<Map<String, Object>>) list : List.of();
    }

    public List<String> actionIds() {
        return actionIds;
    }

    public Set<String> actionIdSet() {
        return new LinkedHashSet<>(actionIds);
    }

    public Map<String, Object> action(String actionId) {
        for (Map<String, Object> action : actions()) {
            if (actionId.equals(action.get("action_id"))) {
                return action;
            }
        }
        throw new NotFoundException("this application publishes no action called " + actionId);
    }
}
