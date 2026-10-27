package com.novapay.paysphere.payments;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.novapay.paysphere.HarnessProperties;
import com.novapay.paysphere.NotFoundException;
import com.novapay.paysphere.ledger.Ledger;
import com.novapay.paysphere.ledger.PaymentRow;
import com.novapay.paysphere.ledger.PaymentState;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * The application's public entry point: what customers use and what synthetic probes exercise.
 *
 * <p>Two headers do real work here. {@code X-Region} routes the payment, which is what makes a
 * single-region incident possible at all. {@code X-SignalForge-Synthetic} marks probe traffic, and
 * the harness honours it by keeping that traffic out of the business counters - a platform whose own
 * health checks moved the KPI it watches would be measuring itself, and the header is the only way
 * an application can be told which requests are not customers.
 */
@RestController
public class PaymentsController {

    public static final String REGION_HEADER = "X-Region";
    public static final String SYNTHETIC_HEADER = "X-SignalForge-Synthetic";

    private final PaymentService payments;
    private final Ledger ledger;
    private final List<String> regions;

    public PaymentsController(
            PaymentService payments, Ledger ledger, HarnessProperties properties) {
        this.payments = payments;
        this.ledger = ledger;
        this.regions = List.copyOf(properties.getRegions());
    }

    /** The harness settles in one currency, so the amount is the only thing a caller chooses. */
    public record PaymentRequest(Long amountMinor) {}

    @PostMapping("/api/payments/{journey}")
    public ResponseEntity<Map<String, Object>> place(
            @PathVariable("journey") String journeyId,
            @RequestHeader(name = REGION_HEADER, required = false) String regionHeader,
            @RequestHeader(name = SYNTHETIC_HEADER, required = false) String syntheticHeader,
            @RequestBody(required = false) PaymentRequest request) {
        Journey journey = Journey.of(journeyId);
        String region = region(regionHeader);
        long amountMinor =
                request == null || request.amountMinor() == null ? 129900L : request.amountMinor();
        boolean synthetic = truthy(syntheticHeader);

        PaymentService.Outcome outcome = payments.place(journey, region, amountMinor, synthetic);
        HttpStatus status =
                refusedByProcessor(outcome)
                        ? HttpStatus.FORBIDDEN
                        : switch (outcome.state()) {
                            case SUCCESS -> HttpStatus.CREATED;
                            case PENDING -> HttpStatus.ACCEPTED;
                            // A payment whose outcome is unknown is not a failure of this request,
                            // and a caller told "failed" would retry and pay twice. 202 says
                            // "accepted, ask again".
                            case UNKNOWN -> HttpStatus.ACCEPTED;
                            default -> HttpStatus.SERVICE_UNAVAILABLE;
                        };
        return ResponseEntity.status(status).body(body(outcome, region, journey));
    }

    /**
     * The processor refused the request on its license, which is a 403 and not a 503: nothing is
     * unavailable, the application is being told no. A probe has to see the same code a customer's
     * client sees, or the platform would be told "down" about a service that is up and refusing.
     */
    private static boolean refusedByProcessor(PaymentService.Outcome outcome) {
        return ProcessorLicense.ERROR_CODE.equals(outcome.errorCode());
    }

    @GetMapping("/api/payments/probe/{journey}")
    public ResponseEntity<Map<String, Object>> probe(
            @PathVariable("journey") String journeyId,
            @RequestParam(name = "region", required = false) String regionParam,
            @RequestHeader(name = REGION_HEADER, required = false) String regionHeader) {
        Journey journey = Journey.of(journeyId);
        String region = region(regionParam != null ? regionParam : regionHeader);
        // Always synthetic, whatever the caller says: this path exists for probes, so counting it
        // as business traffic would be wrong even if somebody curled it by hand.
        PaymentService.Outcome outcome = payments.place(journey, region, 100L, true);
        HttpStatus status =
                refusedByProcessor(outcome)
                        ? HttpStatus.FORBIDDEN
                        : outcome.customerVisibleFailure()
                                ? HttpStatus.SERVICE_UNAVAILABLE
                                : HttpStatus.OK;
        return ResponseEntity.status(status).body(body(outcome, region, journey));
    }

    @GetMapping("/api/payments/{payment_id}")
    public Map<String, Object> get(@PathVariable("payment_id") String paymentId) {
        PaymentRow row =
                ledger.findPayment(paymentId)
                        .orElseThrow(() -> new NotFoundException("no such payment: " + paymentId));
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("payment_id", row.paymentId());
        body.put("region", row.region());
        body.put("journey", row.journey());
        body.put("amount_minor", row.amountMinor());
        body.put("currency", row.currency());
        body.put("state", row.state().name());
        body.put("synthetic", row.synthetic());
        body.put("trace_id", row.traceId());
        body.put("route", row.route());
        body.put("latency_ms", row.latencyMs());
        body.put("created_at", row.createdAt().toString());
        if (row.failureReason() != null) {
            body.put("failure_reason", row.failureReason());
        }
        if (row.state() == PaymentState.UNKNOWN) {
            body.put(
                    "guidance",
                    "the settlement rail never confirmed this payment; run the reconciliation job "
                            + "rather than retrying it");
        }
        return body;
    }

    private Map<String, Object> body(
            PaymentService.Outcome outcome, String region, Journey journey) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("payment_id", outcome.paymentId());
        body.put("state", outcome.state().name());
        body.put("region", region);
        body.put("journey", journey.id());
        body.put("latency_ms", Math.round(outcome.latencyMs()));
        body.put("trace_id", outcome.traceId());
        body.put("route", outcome.route());
        if (outcome.reason() != null) {
            body.put("reason", outcome.reason());
        }
        if (outcome.errorCode() != null) {
            body.put("error_code", outcome.errorCode());
        }
        return body;
    }

    private String region(String raw) {
        if (raw == null || raw.isBlank()) {
            return regions.get(0);
        }
        String region = raw.trim();
        if (!regions.contains(region)) {
            throw new IllegalArgumentException(
                    "unknown region " + raw + "; this application serves " + regions);
        }
        return region;
    }

    private static boolean truthy(String header) {
        return header != null && List.of("1", "true", "yes").contains(header.trim().toLowerCase());
    }
}
