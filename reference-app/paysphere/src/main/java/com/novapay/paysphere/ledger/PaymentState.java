package com.novapay.paysphere.ledger;

/**
 * Payment states.
 *
 * <p>{@link #UNKNOWN} is not an error code. It is what the ledger records when the fabric lost the
 * answer after the request had already reached the settlement rail: the money may or may not have
 * moved, and the only safe thing to write down is that nobody knows yet. Everything interesting the
 * harness can demonstrate - reconciliation, duplicate prevention, "do not retry blindly" - depends
 * on this state existing instead of being rounded to FAILED.
 */
public enum PaymentState {
    INITIATED,
    PENDING,
    SUCCESS,
    FAILED,
    UNKNOWN
}
