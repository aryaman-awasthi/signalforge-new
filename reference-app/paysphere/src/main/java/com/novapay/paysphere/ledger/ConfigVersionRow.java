package com.novapay.paysphere.ledger;

import java.time.OffsetDateTime;
import java.util.Map;

public record ConfigVersionRow(
        String region,
        int version,
        Map<String, Object> settings,
        String source,
        OffsetDateTime appliedAt) {}
