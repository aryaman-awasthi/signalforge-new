package com.novapay.paysphere;

import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.resource.NoResourceFoundException;

/**
 * One error shape for the whole application: {@code {"detail": "..."}}.
 *
 * <p>The status codes matter more than the body. A caller has to be able to tell "you asked for
 * something that does not exist" (404, and for an execution lookup that specifically means "this
 * idempotency key never ran") from "you asked wrongly" (400) from "I broke" (500), because the
 * platform's reconcile logic branches on exactly that distinction.
 */
@RestControllerAdvice
public class ApiExceptionHandler {

    private static final Logger LOG = LoggerFactory.getLogger(ApiExceptionHandler.class);

    @ExceptionHandler(NotFoundException.class)
    public ResponseEntity<Map<String, String>> notFound(NotFoundException exc) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of("detail", exc.getMessage()));
    }

    /**
     * A static path that does not exist is a 404, not a failure of the harness. Without this
     * the catch-all below turned every browser's reflexive {@code /favicon.ico} into a 500 and
     * a stack trace in the log.
     */
    @ExceptionHandler(NoResourceFoundException.class)
    public ResponseEntity<Map<String, String>> noResource(NoResourceFoundException exc) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Map.of("detail", "no such resource: " + exc.getResourcePath()));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<Map<String, String>> badRequest(IllegalArgumentException exc) {
        return ResponseEntity.badRequest()
                .body(Map.of("detail", String.valueOf(exc.getMessage())));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, String>> unexpected(Exception exc) {
        LOG.error("unhandled failure serving a request", exc);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(Map.of("detail", exc.getClass().getSimpleName() + ": " + exc.getMessage()));
    }
}
