package com.novapay.paysphere.security;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

import com.novapay.paysphere.HarnessProperties;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Bearer-key authentication and scope enforcement for {@code /observe/*}.
 *
 * <p>The rule the harness implements is the one the architecture's trust boundary states: telemetry
 * is readable by any of the three keys, an action can only be executed with the remediation key,
 * and chaos can only be touched with the drill key. That is what makes the platform's credential
 * scope floor demonstrable rather than merely asserted - a read-only binding that tried to execute
 * a rollback would be refused here, by the application, not only there.
 *
 * <p>The action ledger sits behind the remediation scope with the actions it records: knowing which
 * remediations ran and when is part of the action surface, not telemetry.
 *
 * <p>Refusals are 401 when the credential is missing or unknown and 403 when a valid credential
 * carries the wrong scope. Both are deliberate: SignalForge's auto-discovery treats either as
 * "this path exists and is protected", which is exactly what is true.
 */
@Component
public class ApiKeyFilter extends OncePerRequestFilter {

    public static final String SCOPE_ATTRIBUTE = "paysphere.scope";

    private final Map<String, Scope> keys = new HashMap<>();

    public ApiKeyFilter(HarnessProperties properties) {
        register(properties.getKeys().getReadOnly(), Scope.READ_ONLY);
        register(properties.getKeys().getRemediation(), Scope.REMEDIATION);
        register(properties.getKeys().getDrill(), Scope.DRILL);
        register(properties.getKeys().getDeploy(), Scope.DEPLOY);
    }

    private void register(String key, Scope scope) {
        if (key != null && !key.isBlank()) {
            keys.put(key.trim(), scope);
        }
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return !path.startsWith("/observe") && !path.startsWith("/api/deploy");
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {

        Scope presented = scopeOf(request.getHeader("Authorization"));
        if (presented == null) {
            refuse(response, 401, "a bearer credential is required for this path");
            return;
        }
        Scope required = requiredScope(request.getRequestURI());
        if (required != null && presented != required) {
            refuse(
                    response,
                    403,
                    "this path requires the "
                            + required.name().toLowerCase()
                            + " credential; the presented credential is "
                            + presented.name().toLowerCase());
            return;
        }
        request.setAttribute(SCOPE_ATTRIBUTE, presented);
        chain.doFilter(request, response);
    }

    /** Null means "any valid credential"; telemetry is readable by all of them. */
    static Scope requiredScope(String path) {
        if (path.startsWith("/api/deploy")) {
            return Scope.DEPLOY;
        }
        if (path.startsWith("/observe/chaos")) {
            return Scope.DRILL;
        }
        if (path.startsWith("/observe/actions")) {
            return Scope.REMEDIATION;
        }
        return null;
    }

    private Scope scopeOf(String header) {
        if (header == null) {
            return null;
        }
        String value = header.trim();
        if (!value.regionMatches(true, 0, "Bearer ", 0, 7)) {
            return null;
        }
        return keys.get(value.substring(7).trim());
    }

    private void refuse(HttpServletResponse response, int status, String detail) throws IOException {
        response.setStatus(status);
        response.setContentType("application/json;charset=UTF-8");
        String body = "{\"detail\":\"" + detail.replace("\"", "'") + "\"}";
        response.getOutputStream().write(body.getBytes(StandardCharsets.UTF_8));
    }
}
