package com.novapay.paysphere;

/** Something the caller named does not exist here. Answered as 404, never as a 400 or a 500. */
public class NotFoundException extends RuntimeException {

    public NotFoundException(String message) {
        super(message);
    }
}
