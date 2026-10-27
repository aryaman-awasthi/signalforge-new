package com.novapay.paysphere.actions;

import java.time.Clock;
import java.time.OffsetDateTime;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

import com.novapay.paysphere.ledger.ExecutionRow;
import com.novapay.paysphere.ledger.Ledger;

import jakarta.annotation.PostConstruct;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Executes a published action once, whatever the caller does.
 *
 * <p>The order of operations is the whole design. The idempotency key is claimed by inserting a
 * {@code running} row <em>before</em> the handler is called, so:
 *
 * <ul>
 *   <li>a caller that times out and retries under the same key gets the original execution, not a
 *       second side effect;
 *   <li>a caller that times out and never retries can still discover what happened, by asking
 *       {@code GET /observe/actions/executions/{key}} - which is exactly what the platform's
 *       reconcile-before-retry does;
 *   <li>a harness that is killed mid-action leaves the row behind in {@code running}, which is an
 *       honest report of an unknown outcome rather than a silently missing one.
 * </ul>
 *
 * <p>The harness refuses to start when the manifest and the handler set disagree, because an action
 * that is published and not implemented is discovered during an incident, and an action that is
 * implemented and not published is a capability nobody reviewed.
 */
@Component
public class ActionExecutor {

    private static final Logger LOG = LoggerFactory.getLogger(ActionExecutor.class);

    public static final String RUNNING = "running";
    public static final String SUCCEEDED = "succeeded";
    public static final String FAILED = "failed";

    /** Exactly the frozen ActionExecution shape. */
    public record Execution(
            String executionId,
            String actionId,
            String idempotencyKey,
            String state,
            OffsetDateTime requestedAt,
            OffsetDateTime startedAt,
            OffsetDateTime finishedAt,
            Map<String, Object> output,
            String error) {}

    private final ActionCatalog catalog;
    private final Map<String, ActionHandler> handlers;
    private final Ledger ledger;
    private final Clock clock;

    public ActionExecutor(
            ActionCatalog catalog, ActionHandlers handlers, Ledger ledger, Clock clock) {
        this.catalog = catalog;
        this.handlers = handlers.handlers();
        this.ledger = ledger;
        this.clock = clock;
    }

    @PostConstruct
    void assertManifestAndCodeAgree() {
        Set<String> published = catalog.actionIdSet();
        Set<String> implemented = new LinkedHashSet<>(handlers.keySet());
        Set<String> unimplemented = new LinkedHashSet<>(published);
        unimplemented.removeAll(implemented);
        Set<String> unpublished = new LinkedHashSet<>(implemented);
        unpublished.removeAll(published);
        if (!unimplemented.isEmpty() || !unpublished.isEmpty()) {
            throw new IllegalStateException(
                    "action manifest and implementation disagree; published with no handler: "
                            + unimplemented
                            + ", implemented but not published: "
                            + unpublished);
        }
        LOG.info("action manifest and implementation agree on {} actions", published.size());
    }

    public Optional<Execution> find(String idempotencyKey) {
        return ledger.findExecution(idempotencyKey).map(ActionExecutor::toExecution);
    }

    public Execution execute(String actionId, Map<String, Object> params, String idempotencyKey) {
        ActionHandler handler = handlers.get(actionId);
        if (handler == null) {
            // Kept as the manifest's own refusal so the message names what is published.
            catalog.action(actionId);
            throw new IllegalArgumentException("no handler for action " + actionId);
        }
        OffsetDateTime requestedAt = OffsetDateTime.now(clock);
        ExecutionRow claim =
                new ExecutionRow(
                        idempotencyKey,
                        "exec_" + UUID.randomUUID().toString().replace("-", "").substring(0, 20),
                        actionId,
                        RUNNING,
                        params == null ? Map.of() : params,
                        Map.of(),
                        null,
                        requestedAt,
                        requestedAt,
                        null);
        if (!ledger.claimExecution(claim)) {
            // Somebody already holds this key: either an earlier attempt that finished, or one that
            // is still running. Either way the answer is that execution, never a second one.
            return find(idempotencyKey)
                    .orElseThrow(
                            () ->
                                    new IllegalStateException(
                                            "idempotency key " + idempotencyKey + " is claimed but unreadable"));
        }

        Map<String, Object> output;
        try {
            output = handler.run(params == null ? Map.of() : params);
        } catch (Exception failure) {
            String message = failure.getClass().getSimpleName() + ": " + failure.getMessage();
            ledger.completeExecution(
                    idempotencyKey, FAILED, Map.of(), message, OffsetDateTime.now(clock));
            LOG.warn("action {} failed under key {}", actionId, idempotencyKey, failure);
            return find(idempotencyKey).orElseThrow();
        }
        ledger.completeExecution(
                idempotencyKey, SUCCEEDED, output == null ? Map.of() : output, null,
                OffsetDateTime.now(clock));
        return find(idempotencyKey).orElseThrow();
    }

    private static Execution toExecution(ExecutionRow row) {
        return new Execution(
                row.executionId(),
                row.actionId(),
                row.idempotencyKey(),
                row.state(),
                row.requestedAt(),
                row.startedAt(),
                row.finishedAt(),
                row.output(),
                row.error());
    }
}
