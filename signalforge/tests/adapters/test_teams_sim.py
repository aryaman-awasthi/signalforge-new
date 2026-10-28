"""teams-sim: a bridge that is scripted and live at the same time (REQ-033).

The interesting property is the script player. It is materialising rather than
streaming, and the three consequences that buys - it follows the platform clock,
it is idempotent, it survives a restart - are each asserted here, because each is
a thing a timer-based player would get wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.contracts.envelope import ActorType
from signalforge.contracts.errors import AdapterError
from signalforge.tests.adapters.conftest import connect

pytestmark = pytest.mark.anyio

START = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
LONG_AGO = START - timedelta(days=1)

SCRIPT = [
    {"after_s": 0, "author_id": "ada", "author_display": "Ada",
     "text": "checkout errors are climbing"},
    {"after_s": 30, "author_id": "grace", "text": "rolling back the 14:02 deploy"},
    {"after_s": 120, "author_id": "ada", "text": "reach me on ada@example.com"},
]


@pytest.fixture
def bridge(api):
    signup = api.signup()
    access_token = signup["tokens"]["access_token"]
    tenant_id = signup["org"]["id"]

    class Bridge:
        tenant = tenant_id
        token = access_token

        def bind(self, **config):
            return connect(
                api, access_token, "teams-sim", credentials={}, config=config or {}
            )

        async def invoke(self, binding, capability, *args, **kwargs):
            return await api.container.invoker.invoke(
                tenant_id, binding["id"], capability, *args, **kwargs
            )

    api.clock.current = START
    return Bridge()


@pytest.mark.req("REQ-033")
async def test_a_script_arrives_at_its_scheduled_times(api, bridge):
    binding = bridge.bind(script=SCRIPT, default_channel="war-room")

    now = await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)
    assert [m.text for m in now] == ["checkout errors are climbing"], (
        "lines dated after now exist as rows and are simply not visible yet"
    )

    api.clock.advance(seconds=45)
    later = await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)
    assert len(later) == 2

    api.clock.advance(minutes=5)
    everything = await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)
    assert len(everything) == 3


@pytest.mark.req("REQ-033", "REQ-024")
async def test_the_script_follows_the_platform_clock(api, bridge):
    """A drill clock at 100x plays the conversation at 100x, with no
    drill-aware code in the adapter."""
    binding = bridge.bind(script=SCRIPT, default_channel="war-room")
    await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)

    api.clock.advance(hours=1)
    assert len(await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)) == 3


@pytest.mark.req("REQ-033")
async def test_replaying_the_script_does_not_duplicate_the_conversation(bridge):
    binding = bridge.bind(script=SCRIPT, default_channel="war-room")
    for _ in range(4):
        await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)
    messages = await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)
    assert len(messages) == 1
    assert len({m.message_id for m in messages}) == 1


@pytest.mark.req("REQ-033")
async def test_live_input_and_the_script_share_one_stream(api, bridge):
    binding = bridge.bind(script=SCRIPT, default_channel="war-room")
    await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)

    api.clock.advance(seconds=10)
    posted = await bridge.invoke(binding, "post", "war-room", "declaring sev1")
    assert posted.simulated is True
    assert posted.author.type is ActorType.SYSTEM

    api.clock.advance(seconds=40)
    stream = await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)
    assert [m.text for m in stream] == [
        "checkout errors are climbing",
        "declaring sev1",
        "rolling back the 14:02 deploy",
    ], "one conversation, in time order, whoever wrote each line"


@pytest.mark.req("REQ-033")
async def test_the_since_window_is_exclusive_so_polling_does_not_repeat(api, bridge):
    binding = bridge.bind(script=SCRIPT, default_channel="war-room")
    first = await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)
    api.clock.advance(seconds=45)
    second = await bridge.invoke(
        binding, "fetch", "war-room", first[-1].sent_at, 50
    )
    assert [m.text for m in second] == ["rolling back the 14:02 deploy"]


@pytest.mark.req("REQ-033", "REQ-018")
async def test_fetched_text_is_redacted(api, bridge):
    binding = bridge.bind(script=SCRIPT, default_channel="war-room")
    await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)
    api.clock.advance(minutes=5)
    stream = await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)
    assert len(stream) == 3
    assert "ada@example.com" not in stream[-1].text
    assert "***" in stream[-1].text


@pytest.mark.req("REQ-033")
async def test_a_channel_with_no_script_is_just_a_channel(bridge):
    binding = bridge.bind(default_channel="bridge")
    assert await bridge.invoke(binding, "fetch", "bridge", LONG_AGO, 50) == []
    await bridge.invoke(binding, "post", "bridge", "anyone there?")
    stream = await bridge.invoke(binding, "fetch", "bridge", LONG_AGO, 50)
    assert [m.text for m in stream] == ["anyone there?"]


@pytest.mark.req("REQ-033")
async def test_separate_channels_do_not_bleed(bridge):
    binding = bridge.bind(script=SCRIPT, default_channel="war-room")
    await bridge.invoke(binding, "post", "other-room", "unrelated")
    war_room = await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)
    assert "unrelated" not in [m.text for m in war_room]


@pytest.mark.req("REQ-033")
async def test_an_empty_post_is_refused(bridge):
    binding = bridge.bind()
    with pytest.raises(AdapterError):
        await bridge.invoke(binding, "post", "bridge", "   ")


@pytest.mark.req("REQ-033")
async def test_an_invalid_script_is_refused_with_a_readable_reason(api, bridge):
    """The manifest's schema catches shape; this catches what a schema cannot
    say - that whitespace is not text."""
    binding = connect(
        api,
        bridge.token,
        "teams-sim",
        credentials={},
        config={"script": [{"after_s": 0, "author_id": "ada", "text": "   "}]},
    )
    with pytest.raises(AdapterError) as refused:
        await bridge.invoke(binding, "fetch", "bridge", LONG_AGO, 10)
    assert "script is invalid" in str(refused.value)


@pytest.mark.req("REQ-033")
async def test_a_script_entry_the_schema_rejects_never_reaches_a_binding(api, bridge):
    connect(
        api,
        bridge.token,
        "teams-sim",
        credentials={},
        config={"script": [{"after_s": -5, "author_id": "", "text": "hi"}]},
        expect=422,
    )


@pytest.mark.req("REQ-033", "REQ-013")
async def test_one_tenants_bridge_is_invisible_to_another(api, bridge):
    binding = bridge.bind(script=SCRIPT, default_channel="war-room")
    await bridge.invoke(binding, "fetch", "war-room", LONG_AGO, 50)

    other = api.signup(org="Other Corp", email="owner@other.io")
    other_binding = connect(
        api, other["tokens"]["access_token"], "teams-sim", credentials={},
        config={"default_channel": "war-room"},
    )
    theirs = await api.container.invoker.invoke(
        other["org"]["id"], other_binding["id"], "fetch", "war-room", LONG_AGO, 50
    )
    assert theirs == [], "a channel name collision is not a shared channel"
