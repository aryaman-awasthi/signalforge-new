package com.novapay.paysphere.ledger;

import java.time.OffsetDateTime;
import java.util.Map;

/** One row of the action ledger; also the shape {@code /observe/actions/executions/...} returns. */
public record ExecutionRow(
        String idempotencyKey,
        String executionId,
        String actionId,
        String state,
        Map<String, Object> params,
        Map<String, Object> output,
        String error,
        OffsetDateTime requestedAt,
        OffsetDateTime startedAt,
        OffsetDateTime finishedAt) {}
