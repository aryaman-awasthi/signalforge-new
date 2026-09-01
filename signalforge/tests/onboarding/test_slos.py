"""S3-T04: SLO targets, the severity matrix and policy defaults (REQ-036).

The floor is tested in test_policy_defaults.py. What is tested here is the rest
of the step: an SLO whose numbers cannot describe a burn is refused, a severity
matrix that cannot express its own worst case is refused, and the matrix itself
is a deterministic function from observed impact to a proposed severity - so the
same impact always proposes the same severity and the rule that matched can be
named.
"""

import pytest

from signalforge.domain.onboarding.profile import (
    Severity,
    SeverityMatrix,
    SeverityRule,
    default_severity_matrix,
)
from signalforge.tests.onboarding.conftest import (  # noqa: F401
    APP_ID,
    SIGNAL_BODY,
    observe,
    owner,
    put_governance,
    put_mapping,
    register_app,
    topics,
)

SLO = {
    "slo_id": "checkout-availability",
    "name": "Checkout availability",
    "objective_pct": 99.9,
    "window_days": 30,
    "journey_id": "checkout",
    "indicator": "checkout_success_ratio",
}


@pytest.mark.req("REQ-036")
def test_slo_targets_carry_their_error_budget(api, observe):
    token, tenant_id = owner(api)
    register_app(api, token)
    put_mapping(api, token, **SIGNAL_BODY)

    profile = put_governance(api, token, slos=[SLO])
    slo = profile["slos"][0]
    assert slo["slo_id"] == "checkout-availability"
    assert slo["error_budget_pct"] == pytest.approx(0.1)
    assert slo["fast_burn_multiplier"] > slo["slow_burn_multiplier"]

    events = topics(api, token, "system.app.governance.updated")
    assert events[-1]["payload"]["slos"] == ["checkout-availability"]
    assert "system.app.governance.updated" in api.audit_topics(tenant_id)


@pytest.mark.req("REQ-036")
def test_an_slo_with_no_error_budget_is_refused(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    refused = put_governance(
        api, token, slos=[dict(SLO, journey_id=None, objective_pct=100)], expect=422
    )
    assert "error budget" in refused["detail"]


@pytest.mark.req("REQ-036")
def test_burn_multipliers_must_be_able_to_tell_fast_from_slow(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    refused = put_governance(
        api,
        token,
        slos=[
            dict(
                SLO,
                journey_id=None,
                fast_burn_multiplier=6.0,
                slow_burn_multiplier=6.0,
            )
        ],
        expect=422,
    )
    assert "fast burn" in refused["detail"]


@pytest.mark.req("REQ-036")
def test_an_slo_cannot_name_a_journey_that_is_not_mapped(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    refused = put_governance(api, token, slos=[SLO], expect=422)
    assert "checkout-availability -> checkout" in refused["detail"]


@pytest.mark.req("REQ-036")
def test_a_severity_matrix_must_be_able_to_express_its_worst_case(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    refused = put_governance(
        api,
        token,
        severity_matrix={
            "rules": [{"severity": "P2", "customer_impacting": False}],
            "default": "P3",
        },
        expect=422,
    )
    assert "P0" in refused["detail"]


@pytest.mark.req("REQ-036")
def test_the_severity_matrix_can_be_edited(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    profile = put_governance(
        api,
        token,
        severity_matrix={
            "rules": [
                {
                    "severity": "P0",
                    "max_journey_tier": 1,
                    "success_rate_below_pct": 90.0,
                    "customer_impacting": True,
                    "description": "tier-1 below 90%",
                },
                {"severity": "P2", "customer_impacting": False},
            ],
            "default": "P3",
        },
    )
    assert [rule["severity"] for rule in profile["severity_matrix"]["rules"]] == [
        "P0", "P2"
    ]
    assert profile["severity_matrix"]["rules"][0]["success_rate_below_pct"] == 90.0


@pytest.mark.req("REQ-036")
def test_a_policy_patch_leaves_the_other_fields_alone(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    before = api.client.get(
        f"/apps/{APP_ID}", headers=api.auth_header(token)
    ).json()["profile"]["policy"]

    after = put_governance(api, token, policy={"stability_window_s": 1_800})["policy"]
    assert after["stability_window_s"] == 1_800
    assert after["approval_ttl_s"] == before["approval_ttl_s"]
    assert after["confidence_threshold"] == before["confidence_threshold"]
    assert after["require_approval_classes"] == before["require_approval_classes"]


# --- the matrix itself, as a pure function ------------------------------------


@pytest.mark.req("REQ-036")
def test_the_default_matrix_classifies_impact_deterministically():
    matrix = default_severity_matrix()

    assert matrix.classify(
        journey_tier=1,
        regions_affected=2,
        success_rate_pct=45.0,
        customer_impacting=True,
    ) is Severity.P0
    assert matrix.classify(
        journey_tier=1,
        regions_affected=1,
        success_rate_pct=88.0,
        customer_impacting=True,
    ) is Severity.P1
    assert matrix.classify(
        journey_tier=2,
        regions_affected=1,
        success_rate_pct=99.0,
        customer_impacting=False,
    ) is Severity.P2
    assert matrix.classify() is Severity.P2, (
        "no journey, no impact: the no-customer-impact rule matches"
    )


@pytest.mark.req("REQ-036")
def test_the_most_severe_matching_rule_wins_and_the_result_is_stable():
    matrix = SeverityMatrix(
        rules=(
            SeverityRule(severity=Severity.P1, customer_impacting=True),
            SeverityRule(severity=Severity.P0, max_journey_tier=1),
        )
    )
    assert [rule.severity for rule in matrix.rules] == [Severity.P0, Severity.P1]
    assert matrix.classify(journey_tier=1, customer_impacting=True) is Severity.P0
    assert matrix.classify(journey_tier=3, customer_impacting=True) is Severity.P1
    assert matrix.classify(journey_tier=3, customer_impacting=False) is Severity.P3
