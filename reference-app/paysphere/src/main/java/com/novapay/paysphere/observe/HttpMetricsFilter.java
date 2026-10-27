package com.novapay.paysphere.observe;

import java.io.IOException;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Counts every request as {@code http_requests_total{endpoint,method,status}}.
 *
 * <p>The endpoint label is a <em>template</em>, never the raw URI. A payment id or an idempotency key
 * in a metric label mints a new time series per request, which turns a metrics endpoint into a
 * denial-of-service surface and makes every dashboard useless at the same time. The mapping below is
 * a closed set, so a caller cannot invent a label value.
 */
@Component
public class HttpMetricsFilter extends OncePerRequestFilter {

    private final MetricsPublisher publisher;

    public HttpMetricsFilter(MetricsPublisher publisher) {
        this.publisher = publisher;
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        try {
            chain.doFilter(request, response);
        } finally {
            publisher.countHttp(
                    endpointOf(request.getMethod(), request.getRequestURI()),
                    request.getMethod(),
                    response.getStatus());
        }
    }

    static String endpointOf(String method, String uri) {
        if (uri.startsWith("/observe/actions/executions/")) {
            return "/observe/actions/executions/{key}";
        }
        if (uri.startsWith("/observe/actions/") && uri.endsWith("/execute")) {
            return "/observe/actions/{id}/execute";
        }
        if (uri.startsWith("/observe/chaos/injections/") && uri.endsWith("/abort")) {
            return "/observe/chaos/injections/{id}/abort";
        }
        if (uri.startsWith("/observe/chaos/injections/")) {
            return "/observe/chaos/injections/{id}";
        }
        if (uri.startsWith("/observe/chaos/") && uri.endsWith("/inject")) {
            return "/observe/chaos/{id}/inject";
        }
        if (uri.startsWith("/api/payments/probe/")) {
            return "/api/payments/probe/{journey}";
        }
        if (uri.startsWith("/api/payments/")) {
            return "POST".equalsIgnoreCase(method)
                    ? "/api/payments/{journey}"
                    : "/api/payments/{payment_id}";
        }
        return switch (uri) {
            case "/observe/metrics",
                    "/observe/logs",
                    "/observe/traces",
                    "/observe/health",
                    "/observe/topology",
                    "/observe/actions",
                    "/observe/chaos" -> uri;
            default -> "other";
        };
    }
}
