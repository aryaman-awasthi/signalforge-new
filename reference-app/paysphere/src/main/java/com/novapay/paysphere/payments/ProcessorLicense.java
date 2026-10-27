package com.novapay.paysphere.payments;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;

import org.springframework.stereotype.Component;

/**
 * The payment processor SDK's license, and whether it is still valid.
 *
 * <p>A real payments application authorises through a vendor SDK that carries a time-limited
 * license key. When the key lapses the SDK does not slow down or drop a share of requests - it
 * refuses every one of them, immediately, with an error that names the license. That makes it a
 * different <em>kind</em> of incident from anything the fabric can produce: no link is down, no pool
 * is full, latency is excellent, and the success rate is zero. The platform has to name the cause
 * from what the application says, not from the shape of the metrics.
 *
 * <p>The license is valid for a year from startup. The {@code license-expiry} chaos scenario expires
 * it; the {@code renew-license-key} action renews it. Both are idempotent.
 */
@Component
public class ProcessorLicense {

    public static final String COMPONENT = "payment-processor-license";
    public static final String ERROR_CODE = "LICENSE_EXPIRED";
    public static final String PROCESSOR = "processor-sdk";

    private static final Duration DEFAULT_TERM = Duration.ofDays(365);

    private final Clock clock;
    private volatile Instant validUntil;
    private volatile Instant lastRenewedAt;

    public ProcessorLicense(Clock clock) {
        this.clock = clock;
        this.lastRenewedAt = Instant.now(clock);
        this.validUntil = lastRenewedAt.plus(DEFAULT_TERM);
    }

    public boolean expired() {
        return !Instant.now(clock).isBefore(validUntil);
    }

    public Instant validUntil() {
        return validUntil;
    }

    public Instant lastRenewedAt() {
        return lastRenewedAt;
    }

    /** Seconds until the license lapses; zero once it has. */
    public long secondsRemaining() {
        long seconds = Duration.between(Instant.now(clock), validUntil).getSeconds();
        return Math.max(0L, seconds);
    }

    /** Lapse the license now. Repeating it changes nothing. */
    public void expire() {
        Instant now = Instant.now(clock);
        if (validUntil.isAfter(now)) {
            validUntil = now;
        }
    }

    /** Install a renewed key valid for the given number of days from now. */
    public Instant renew(int days) {
        if (days < 1) {
            throw new IllegalArgumentException("a license term is at least one day, got " + days);
        }
        Instant now = Instant.now(clock);
        lastRenewedAt = now;
        validUntil = now.plus(Duration.ofDays(days));
        return validUntil;
    }

    /** The sentence the SDK puts in its refusal, and health puts in its detail. */
    public String refusalDetail() {
        return "payment processor license expired at " + validUntil
                + "; the processor SDK refuses every request with " + ERROR_CODE
                + " until a renewed license key is installed";
    }
}
