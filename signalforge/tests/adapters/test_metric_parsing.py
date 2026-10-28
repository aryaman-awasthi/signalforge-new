"""Prometheus text parsing, selectors and the prometheus adapter (REQ-031).

The parser is pure, so it is tested against the lines that actually break
parsers - escaped label values, `+Inf`, sample timestamps, a metric with no
labels - rather than against a server. The adapter is tested against a
Prometheus-shaped server, because what matters there is the translation into
canonical series and what happens when the server refuses the query.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from signalforge.contracts.errors import AdapterError
from signalforge.domain.integrations.promtext import (
    parse_exposition,
    parse_selector,
    select,
)
from signalforge.tests.adapters.conftest import ObserveHarness, connect

AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


# --- the parser ---------------------------------------------------------------


@pytest.mark.req("REQ-031")
def test_comments_and_type_lines_are_skipped():
    text = "# HELP x help\n# TYPE x counter\n\nx 1\n"
    series = parse_exposition(text, AT)
    assert len(series) == 1
    assert series[0].name == "x"
    assert series[0].labels == {}
    assert series[0].points[0].value == 1.0


@pytest.mark.req("REQ-031")
def test_labels_are_unescaped():
    text = 'http_requests{path="/a\\"b",note="line\\nbreak",win="C:\\\\tmp"} 3\n'
    series = parse_exposition(text, AT)
    assert series[0].labels == {"path": '/a"b', "note": "line\nbreak", "win": "C:\\tmp"}


@pytest.mark.req("REQ-031")
def test_special_values_survive():
    series = parse_exposition("a +Inf\nb -Inf\nc NaN\n", AT)
    values = {one.name: one.points[0].value for one in series}
    assert values["a"] == math.inf
    assert values["b"] == -math.inf
    assert math.isnan(values["c"])


@pytest.mark.req("REQ-031")
def test_a_sample_that_dates_itself_keeps_its_own_timestamp():
    """An exporter that stamps its data knows better than the poller does."""
    series = parse_exposition("x 5 1756728000000\n", AT)
    assert series[0].points[0].ts == datetime(2025, 9, 1, 12, 0, tzinfo=timezone.utc)
    assert series[0].points[0].ts != AT, "the poller's clock did not win"


@pytest.mark.req("REQ-031")
def test_a_malformed_line_raises_rather_than_being_dropped():
    """A metrics feed that quietly loses rows is a detection bug waiting to
    happen: the anomaly is in the row that vanished."""
    with pytest.raises(AdapterError) as refused:
        parse_exposition("x 1\nthis is not a sample\n", AT)
    assert "malformed" in str(refused.value)


@pytest.mark.req("REQ-031")
def test_a_malformed_label_set_raises():
    with pytest.raises(AdapterError):
        parse_exposition('x{broken=} 1\n', AT)


@pytest.mark.req("REQ-030", "REQ-031")
def test_max_rows_bounds_what_is_parsed():
    text = "".join(f'x{{i="{n}"}} {n}\n' for n in range(10))
    with pytest.raises(AdapterError) as refused:
        parse_exposition(text, AT, max_rows=5)
    assert "max_rows" in str(refused.value)


@pytest.mark.req("REQ-031")
def test_repeated_name_and_labels_accumulate_into_one_series():
    series = parse_exposition('x{a="1"} 1 1756728000000\nx{a="1"} 2 1756728015000\n', AT)
    assert len(series) == 1
    assert [point.value for point in series[0].points] == [1.0, 2.0]


@pytest.mark.req("REQ-031")
def test_an_empty_selector_is_everything_rather_than_a_guess():
    series = parse_exposition("a 1\nb 2\n", AT)
    assert len(select(series, "")) == 2


@pytest.mark.req("REQ-031")
def test_selector_matchers():
    series = parse_exposition(
        'x{region="eu-west-1",tier="gold"} 1\n'
        'x{region="us-east-1",tier="gold"} 2\n'
        'y{region="eu-west-1"} 3\n',
        AT,
    )
    assert len(select(series, "x")) == 2
    assert len(select(series, 'x{region="eu-west-1"}')) == 1
    assert len(select(series, 'x{region!="eu-west-1"}')) == 1
    assert len(select(series, '{region=~"eu-.*"}')) == 2
    assert len(select(series, 'x{region!~"eu-.*"}')) == 1
    assert len(select(series, 'x{tier="silver"}')) == 0


@pytest.mark.req("REQ-031")
def test_an_unparseable_selector_says_what_is_supported():
    with pytest.raises(AdapterError) as refused:
        parse_selector("sum(rate(x[5m]))")
    assert "=~" in str(refused.value)


@pytest.mark.req("REQ-031")
def test_a_bad_regex_matcher_is_refused_at_parse_time():
    with pytest.raises(AdapterError):
        parse_selector('x{region=~"eu-(["}')


# --- the prometheus adapter ---------------------------------------------------


@pytest.fixture
def prom(api, observe):
    signup = api.signup()
    token = signup["tokens"]["access_token"]
    tenant_id = signup["org"]["id"]
    binding = connect(
        api,
        token,
        "prometheus",
        config={"base_url": ObserveHarness.PROMETHEUS_URL},
        credentials={},
    )

    class Prom:
        state = observe.prometheus

        async def invoke(self, capability, *args, **kwargs):
            return await api.container.invoker.invoke(
                tenant_id, binding["id"], capability, *args, **kwargs
            )

    return Prom()


@pytest.mark.anyio
@pytest.mark.req("REQ-031")
async def test_prometheus_range_query_becomes_canonical_series(prom):
    series = await prom.invoke(
        "query_range", "http_requests_total", AT - timedelta(minutes=1), AT, 15.0
    )
    assert {one.name for one in series} == {"http_requests_total"}
    eu = next(one for one in series if one.labels == {"region": "eu-west-1"})
    assert [point.value for point in eu.points] == [1200.0, 1260.0]
    assert eu.points[0].ts.tzinfo is timezone.utc
    assert prom.state.last_params["step"] == "15.0"


@pytest.mark.anyio
@pytest.mark.req("REQ-031")
async def test_prometheus_instant_query_reads_the_last_sample(prom):
    series = await prom.invoke("query_instant", "http_requests_total", AT)
    eu = next(one for one in series if one.labels == {"region": "eu-west-1"})
    assert [point.value for point in eu.points] == [1260.0]


@pytest.mark.anyio
@pytest.mark.req("REQ-031", "REQ-040")
async def test_the_empty_selector_scrapes_every_series(prom):
    """The platform's "give me everything" reaches Prometheus as an expression.

    `parse_selector` fixes the contract: an empty selector is every series, and
    both scrape adapters honour it. Prometheus has no empty expression - it
    answers 400 `no expression found in input` - so the adapter owes the
    translation. Without it the onboarding connection test fails on the metrics
    capability and, worse, every five-second poll fails the same way, so no
    rollup is ever written and no anomaly can ever be raised.
    """
    series = await prom.invoke("query_instant", "", AT)
    assert prom.state.last_params["query"].strip(), "an empty PromQL expression is a 400"
    assert {one.name for one in series} == {"http_requests_total"}


@pytest.mark.anyio
@pytest.mark.req("REQ-031")
async def test_the_empty_selector_scrapes_every_series_over_a_range(prom):
    series = await prom.invoke(
        "query_range", "", AT - timedelta(minutes=1), AT, 15.0
    )
    assert prom.state.last_params["query"].strip()
    assert {one.name for one in series} == {"http_requests_total"}


@pytest.mark.anyio
@pytest.mark.req("REQ-031")
async def test_a_named_selector_is_passed_through_untouched(prom):
    """The translation is for the empty case only; a real expression is the
    caller's and the adapter does not rewrite it."""
    await prom.invoke("query_instant", 'http_requests_total{region="eu-west-1"}', AT)
    assert prom.state.last_params["query"] == 'http_requests_total{region="eu-west-1"}'


@pytest.mark.anyio
@pytest.mark.req("REQ-031")
async def test_a_nan_sample_is_kept_rather_than_dropped(prom):
    series = await prom.invoke(
        "query_range", "http_requests_total", AT - timedelta(minutes=1), AT, 15.0
    )
    us = next(one for one in series if one.labels == {"region": "us-east-1"})
    assert math.isnan(us.points[1].value), "a gap is data; dropping it invents continuity"


@pytest.mark.anyio
@pytest.mark.req("REQ-031")
async def test_a_refused_query_names_the_reason(prom):
    prom.state.error = "parse error: unexpected ]"
    with pytest.raises(AdapterError) as refused:
        await prom.invoke("query_instant", "x[", AT)
    assert "parse error" in str(refused.value)


@pytest.mark.anyio
@pytest.mark.req("REQ-031")
async def test_an_error_envelope_returned_with_200_is_still_an_error(prom):
    """"The query was wrong" and "there is no data" are answers a responder
    must never confuse."""
    prom.state.error = "bad_data: invalid parameter"
    prom.state.error_status = 200
    with pytest.raises(AdapterError) as refused:
        await prom.invoke("query_instant", "x", AT)
    assert "Prometheus refused" in str(refused.value)


@pytest.mark.anyio
@pytest.mark.req("REQ-031")
async def test_an_over_wide_range_is_refused_before_the_call(prom):
    with pytest.raises(AdapterError) as refused:
        await prom.invoke(
            "query_range", "x", AT - timedelta(days=30), AT, 1.0
        )
    assert "widen the step" in str(refused.value)


@pytest.mark.anyio
@pytest.mark.req("REQ-031")
async def test_a_backwards_or_zero_step_range_is_refused(prom):
    with pytest.raises(AdapterError):
        await prom.invoke("query_range", "x", AT, AT - timedelta(minutes=1), 15.0)
    with pytest.raises(AdapterError):
        await prom.invoke("query_range", "x", AT - timedelta(minutes=1), AT, 0.0)


@pytest.mark.anyio
@pytest.mark.req("REQ-031")
async def test_an_unreachable_prometheus_reads_as_unavailable(api, observe):
    from signalforge.contracts.errors import AdapterUnavailableError

    signup = api.signup(org="Other", email="o@other.io")
    token = signup["tokens"]["access_token"]
    binding = connect(
        api,
        token,
        "prometheus",
        config={"base_url": "http://nothing-here.test"},
        credentials={},
    )
    with pytest.raises(AdapterUnavailableError):
        await api.container.invoker.invoke(
            signup["org"]["id"], binding["id"], "ping"
        )
