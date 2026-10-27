package com.novapay.paysphere.ledger;

import java.time.OffsetDateTime;
import java.util.Map;

public record InjectionRow(
        String injectionId,
        String idempotencyKey,
        String scenarioId,
        String target,
        Map<String, Object> params,
        String state,
        String scope,
        OffsetDateTime expiresAt,
        OffsetDateTime startedAt,
        OffsetDateTime endedAt) {}
