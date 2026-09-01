"""S3-T04: the baseline step (REQ-036).

Learning a baseline is the first thing in the product that reads real telemetry
repeatedly, so its failure design matters more than its arithmetic:

- **partial completion** - a signal whose selector matched nothing is reported by
  name and the signals that did match are still stored;
- **nothing is invented** - fewer usable samples than the domain's floor is a
  miss, not a baseline computed from two points;
- **a failed run changes nothing** - a target that stops answering mid-step
  leaves the previous baselines exactly as they were;
- **a baseline belongs to its signal** - re-mapping the signals drops the
  baselines that no longer have one, rather than keeping a number nothing can be
  compared against.
"""

import copy

import pytest

from signalforge.tests.onboarding.conftest import (  # noqa: F401
    APP_ID,
    SIGNAL_BODY,
    connect,
    observe,
    owner,
    put_mapping,
    register_app,
    topics,
)


def learn(api, token, app_id=APP_ID, *, expect=200, **body) -> dict:
    response = api.client.post(
        f"/apps/{app_id}/baseline", json=body, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def onboard(api, token, **mapping):
    register_app(api, token)
    put_mapping(api, token, **(mapping or SIGNAL_BODY))
    connect(api, token, "http-observe-metrics", app_id=APP_ID)


@pytest.mark.req("REQ-036")
def test_a_baseline_is_learned_from_repeated_scrapes(api, observe):
    token, tenant_id = owner(api)
    onboard(api, token)

    run = learn(api, token, samples=5)
    assert run["samples"] == 5
    assert run["missed"] == []

    by_key = {baseline["signal_key"]: baseline for baseline in run["learned"]}
    assert set(by_key) == {"errors", "payments-success"}

    errors = by_key["errors"]
    assert errors["series_count"] == 2, "the checkout route in both regions"
    assert errors["samples"] == 10, "two series across five scrapes"
    assert errors["mean"] == pytest.approx(1000.0)
    assert errors["minimum"] == 800.0 and errors["maximum"] == 1200.0
    assert errors["source"] == "http-observe-metrics"

    events = topics(api, token, "system.app.baseline.learned")
    assert sorted(events[0]["payload"]["learned"]) == ["errors", "payments-success"]
    assert "system.app.baseline.learned" in api.audit_topics(tenant_id)


@pytest.mark.req("REQ-036")
def test_a_signal_that_matched_nothing_is_named_and_the_rest_still_land(api, observe):
    token, _tenant_id = owner(api)
    body = copy.deepcopy(SIGNAL_BODY)
    body["signals"].append(
        {
            "key": "saturation",
            "signal": "saturation",
            "selector": "queue_depth_that_does_not_exist",
            "direction": "higher_is_worse",
        }
    )
    onboard(api, token, **body)

    run = learn(api, token, samples=3)
    assert [miss["signal_key"] for miss in run["missed"]] == ["saturation"]
    assert "matched 0 series" in run["missed"][0]["reason"]
    assert {baseline["signal_key"] for baseline in run["learned"]} == {
        "errors", "payments-success"
    }

    profile = api.client.get(
        f"/apps/{APP_ID}", headers=api.auth_header(token)
    ).json()["profile"]
    assert len(profile["baselines"]) == 2


@pytest.mark.req("REQ-036")
def test_too_few_samples_is_refused_rather_than_averaged(api, observe):
    token, _tenant_id = owner(api)
    onboard(api, token)

    refused = learn(api, token, samples=2, expect=422)
    assert "samples must be between" in refused["detail"]


@pytest.mark.req("REQ-036")
def test_learning_needs_signals_and_a_metrics_connector(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    no_signals = learn(api, token, expect=422)
    assert "map at least one golden signal" in no_signals["detail"]

    put_mapping(api, token, **SIGNAL_BODY)
    no_connector = learn(api, token, expect=422)
    assert "no enabled metrics connector" in no_connector["detail"]


@pytest.mark.req("REQ-036")
def test_a_failed_run_leaves_the_previous_baselines_untouched(api, observe):
    token, _tenant_id = owner(api)
    onboard(api, token)
    first = learn(api, token, samples=3)
    assert len(first["learned"]) == 2

    observe.state.fail("/observe/metrics", 503)
    response = api.client.post(
        f"/apps/{APP_ID}/baseline", json={"samples": 3}, headers=api.auth_header(token)
    )
    assert response.status_code == 502

    profile = api.client.get(
        f"/apps/{APP_ID}", headers=api.auth_header(token)
    ).json()["profile"]
    assert len(profile["baselines"]) == 2
    assert {b["signal_key"] for b in profile["baselines"]} == {
        "errors", "payments-success"
    }


@pytest.mark.req("REQ-036")
def test_remapping_a_signal_away_drops_its_baseline(api, observe):
    token, _tenant_id = owner(api)
    onboard(api, token)
    learn(api, token, samples=3)

    body = copy.deepcopy(SIGNAL_BODY)
    body["signals"] = body["signals"][:1]
    profile = put_mapping(api, token, **body)

    assert [baseline["signal_key"] for baseline in profile["baselines"]] == ["errors"]
    events = topics(api, token, "system.app.mapping.updated")
    assert events[-1]["payload"]["baselines_dropped"] == 1


@pytest.mark.req("REQ-036")
def test_a_second_run_replaces_the_baseline_for_the_signals_it_reached(api, observe):
    token, _tenant_id = owner(api)
    onboard(api, token)
    learn(api, token, samples=3)

    observe.state.metrics_text = (
        'http_requests_total{route="/checkout",region="eu-west-1"} 10\n'
        'checkout_success_ratio{region="eu-west-1"} 0.5\n'
    )
    second = learn(api, token, samples=3)
    errors = next(b for b in second["learned"] if b["signal_key"] == "errors")
    assert errors["mean"] == pytest.approx(10.0)
    assert errors["series_count"] == 1

    profile = api.client.get(
        f"/apps/{APP_ID}", headers=api.auth_header(token)
    ).json()["profile"]
    stored = next(b for b in profile["baselines"] if b["signal_key"] == "errors")
    assert stored["mean"] == pytest.approx(10.0)
