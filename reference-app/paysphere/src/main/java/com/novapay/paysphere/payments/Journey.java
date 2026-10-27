package com.novapay.paysphere.payments;

import java.util.Locale;

/** The four customer journeys. The journey is a metric label, so its id is its wire form. */
public enum Journey {
    UPI("upi", 1),
    CARD("card", 1),
    WALLET("wallet", 2),
    REFUND("refund", 2);

    private final String id;
    private final int tier;

    Journey(String id, int tier) {
        this.id = id;
        this.tier = tier;
    }

    public String id() {
        return id;
    }

    public int tier() {
        return tier;
    }

    public static Journey of(String raw) {
        String wanted = raw == null ? "" : raw.trim().toLowerCase(Locale.ROOT);
        for (Journey journey : values()) {
            if (journey.id.equals(wanted)) {
                return journey;
            }
        }
        throw new IllegalArgumentException(
                "no such journey: " + raw + "; this application serves upi, card, wallet and refund");
    }
}
