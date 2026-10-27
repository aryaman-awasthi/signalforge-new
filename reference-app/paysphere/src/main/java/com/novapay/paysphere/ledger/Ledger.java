package com.novapay.paysphere.ledger;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.springframework.boot.sql.init.dependency.DependsOnDatabaseInitialization;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Component;

/**
 * Every durable thing the harness knows, in H2.
 *
 * <p>Two properties here exist for the platform's benefit rather than the harness's. First, an
 * execution row is inserted <em>before</em> the action's side effect is attempted and updated
 * afterwards, so a caller that timed out mid-action can still discover what happened by its own
 * idempotency key - that lookup is the platform's reconcile-before-retry primitive. Second, a
 * payment that was lost at the rail is stored as UNKNOWN rather than FAILED, so the reconciliation
 * job has something real to reconcile instead of a guess already written down.
 */
@Component
@DependsOnDatabaseInitialization
public class Ledger {

    private final JdbcTemplate jdbc;
    private final ObjectMapper json;

    public Ledger(JdbcTemplate jdbc, ObjectMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    // --- payments ------------------------------------------------------------------------

    public void insertPayment(PaymentRow row) {
        jdbc.update(
                "insert into payments (payment_id, region, journey, amount_minor, currency, state,"
                        + " synthetic, trace_id, route, latency_ms, failure_reason, created_at,"
                        + " updated_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                row.paymentId(),
                row.region(),
                row.journey(),
                row.amountMinor(),
                row.currency(),
                row.state().name(),
                row.synthetic(),
                row.traceId(),
                row.route(),
                row.latencyMs(),
                row.failureReason(),
                row.createdAt(),
                row.updatedAt());
    }

    public void updatePaymentState(
            String paymentId, PaymentState state, String failureReason, OffsetDateTime at) {
        jdbc.update(
                "update payments set state = ?, failure_reason = ?, updated_at = ? where payment_id = ?",
                state.name(),
                failureReason,
                at,
                paymentId);
    }

    public Optional<PaymentRow> findPayment(String paymentId) {
        return jdbc
                .query("select * from payments where payment_id = ?", PAYMENT, paymentId)
                .stream()
                .findFirst();
    }

    public List<PaymentRow> paymentsInState(String region, PaymentState state, int limit) {
        return jdbc.query(
                "select * from payments where region = ? and state = ? order by created_at limit ?",
                PAYMENT,
                region,
                state.name(),
                limit);
    }

    public long countPayments(String region, PaymentState state) {
        Long count =
                jdbc.queryForObject(
                        "select count(*) from payments where region = ? and state = ?",
                        Long.class,
                        region,
                        state.name());
        return count == null ? 0L : count;
    }

    // --- settlements ---------------------------------------------------------------------

    public void insertSettlement(
            String settlementId,
            String paymentId,
            String region,
            String state,
            OffsetDateTime createdAt,
            OffsetDateTime settledAt) {
        jdbc.update(
                "insert into settlements (settlement_id, payment_id, region, state, created_at,"
                        + " settled_at) values (?,?,?,?,?,?)",
                settlementId,
                paymentId,
                region,
                state,
                createdAt,
                settledAt);
    }

    public long countSettlements(String region, String state) {
        Long count =
                jdbc.queryForObject(
                        "select count(*) from settlements where region = ? and state = ?",
                        Long.class,
                        region,
                        state);
        return count == null ? 0L : count;
    }

    // --- executions ----------------------------------------------------------------------

    public Optional<ExecutionRow> findExecution(String idempotencyKey) {
        return jdbc
                .query(
                        "select * from executions where idempotency_key = ?",
                        EXECUTION,
                        idempotencyKey)
                .stream()
                .findFirst();
    }

    /**
     * Claim the idempotency key. False means another caller already holds it, which is how two
     * simultaneous retries of the same request become one execution rather than two.
     */
    public boolean claimExecution(ExecutionRow row) {
        try {
            jdbc.update(
                    "insert into executions (idempotency_key, execution_id, action_id, state,"
                            + " params, output, error, requested_at, started_at, finished_at)"
                            + " values (?,?,?,?,?,?,?,?,?,?)",
                    row.idempotencyKey(),
                    row.executionId(),
                    row.actionId(),
                    row.state(),
                    write(row.params()),
                    null,
                    null,
                    row.requestedAt(),
                    row.startedAt(),
                    null);
            return true;
        } catch (DuplicateKeyException already) {
            return false;
        }
    }

    public void completeExecution(
            String idempotencyKey,
            String state,
            Map<String, Object> output,
            String error,
            OffsetDateTime finishedAt) {
        jdbc.update(
                "update executions set state = ?, output = ?, error = ?, finished_at = ?"
                        + " where idempotency_key = ?",
                state,
                write(output),
                error == null ? null : error.substring(0, Math.min(error.length(), 1024)),
                finishedAt,
                idempotencyKey);
    }

    // --- chaos injections ----------------------------------------------------------------

    public Optional<InjectionRow> findInjectionByKey(String idempotencyKey) {
        return jdbc
                .query(
                        "select * from chaos_injections where idempotency_key = ?",
                        INJECTION,
                        idempotencyKey)
                .stream()
                .findFirst();
    }

    public Optional<InjectionRow> findInjection(String injectionId) {
        return jdbc
                .query("select * from chaos_injections where injection_id = ?", INJECTION, injectionId)
                .stream()
                .findFirst();
    }

    public List<InjectionRow> injectionsInState(String state) {
        return jdbc.query("select * from chaos_injections where state = ?", INJECTION, state);
    }

    public boolean claimInjection(InjectionRow row) {
        try {
            jdbc.update(
                    "insert into chaos_injections (injection_id, idempotency_key, scenario_id,"
                            + " target, params, state, scope, expires_at, started_at, ended_at)"
                            + " values (?,?,?,?,?,?,?,?,?,?)",
                    row.injectionId(),
                    row.idempotencyKey(),
                    row.scenarioId(),
                    row.target(),
                    write(row.params()),
                    row.state(),
                    row.scope(),
                    row.expiresAt(),
                    row.startedAt(),
                    row.endedAt());
            return true;
        } catch (DuplicateKeyException already) {
            return false;
        }
    }

    public void endInjection(String injectionId, String state, OffsetDateTime endedAt) {
        jdbc.update(
                "update chaos_injections set state = ?, ended_at = ? where injection_id = ?",
                state,
                endedAt,
                injectionId);
    }

    // --- config versions -----------------------------------------------------------------

    public void saveConfigVersion(
            String region,
            int version,
            Map<String, Object> settings,
            String source,
            OffsetDateTime appliedAt) {
        jdbc.update(
                "insert into config_versions (region, version, settings, source, applied_at)"
                        + " values (?,?,?,?,?)",
                region,
                version,
                write(settings),
                source,
                appliedAt);
    }

    public Optional<ConfigVersionRow> findConfigVersion(String region, int version) {
        return jdbc
                .query(
                        "select * from config_versions where region = ? and version = ?",
                        CONFIG_VERSION,
                        region,
                        version)
                .stream()
                .findFirst();
    }

    public List<ConfigVersionRow> configHistory(String region) {
        return jdbc.query(
                "select * from config_versions where region = ? order by version", CONFIG_VERSION, region);
    }

    // --- json helpers --------------------------------------------------------------------

    private String write(Map<String, Object> value) {
        if (value == null) {
            return null;
        }
        try {
            return json.writeValueAsString(value);
        } catch (Exception exc) {
            throw new IllegalStateException("cannot serialise ledger json", exc);
        }
    }

    Map<String, Object> read(String raw) {
        if (raw == null || raw.isBlank()) {
            return Map.of();
        }
        try {
            return json.readValue(raw, new TypeReference<Map<String, Object>>() {});
        } catch (Exception exc) {
            throw new IllegalStateException("cannot deserialise ledger json", exc);
        }
    }

    // --- row mappers ---------------------------------------------------------------------

    private static OffsetDateTime at(java.sql.ResultSet rs, String column) throws java.sql.SQLException {
        return rs.getObject(column, OffsetDateTime.class);
    }

    private final RowMapper<PaymentRow> PAYMENT =
            (rs, i) ->
                    new PaymentRow(
                            rs.getString("payment_id"),
                            rs.getString("region"),
                            rs.getString("journey"),
                            rs.getLong("amount_minor"),
                            rs.getString("currency"),
                            PaymentState.valueOf(rs.getString("state")),
                            rs.getBoolean("synthetic"),
                            rs.getString("trace_id"),
                            rs.getString("route"),
                            rs.getDouble("latency_ms"),
                            rs.getString("failure_reason"),
                            at(rs, "created_at"),
                            at(rs, "updated_at"));

    private final RowMapper<ExecutionRow> EXECUTION =
            (rs, i) ->
                    new ExecutionRow(
                            rs.getString("idempotency_key"),
                            rs.getString("execution_id"),
                            rs.getString("action_id"),
                            rs.getString("state"),
                            read(rs.getString("params")),
                            read(rs.getString("output")),
                            rs.getString("error"),
                            at(rs, "requested_at"),
                            at(rs, "started_at"),
                            at(rs, "finished_at"));

    private final RowMapper<InjectionRow> INJECTION =
            (rs, i) ->
                    new InjectionRow(
                            rs.getString("injection_id"),
                            rs.getString("idempotency_key"),
                            rs.getString("scenario_id"),
                            rs.getString("target"),
                            read(rs.getString("params")),
                            rs.getString("state"),
                            rs.getString("scope"),
                            at(rs, "expires_at"),
                            at(rs, "started_at"),
                            at(rs, "ended_at"));

    private final RowMapper<ConfigVersionRow> CONFIG_VERSION =
            (rs, i) ->
                    new ConfigVersionRow(
                            rs.getString("region"),
                            rs.getInt("version"),
                            read(rs.getString("settings")),
                            rs.getString("source"),
                            at(rs, "applied_at"));
}
