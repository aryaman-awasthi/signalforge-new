package com.novapay.paysphere.security;

/**
 * The three credential scopes the harness issues, matching the credential scopes the platform's
 * adapter manifests declare. They are not a hierarchy: a remediation key does not imply drill.
 */
public enum Scope {
    READ_ONLY,
    REMEDIATION,
    DRILL,
    /** The application's own deployment pipeline. Nothing observability-related may hold it. */
    DEPLOY
}
