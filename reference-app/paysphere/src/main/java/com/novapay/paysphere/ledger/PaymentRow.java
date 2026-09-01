package com.novapay.paysphere.ledger;

import java.time.OffsetDateTime;

public record PaymentRow(
        String paymentId,
        String region,
        String journey,
        long amountMinor,
        String currency,
        PaymentState state,
        boolean synthetic,
        String traceId,
        String route,
        double latencyMs,
        String failureReason,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt) {}
