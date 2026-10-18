"""Independent orchestrator tests for cogs/playback.py state machine (Yuzu revamp, P1).

Network-free: no Discord connection, no stream probes over the wire.
Run:  python tests/test_playback_state.py    (exit 0 = all pass)
"""
import asyncio
import logging
import os
import sys
from types import SimpleNamespace

import discord

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
logging.disable(logging.CRITICAL)  # keep output to PASS/FAIL lines only

import config  # real config (loads .env values but prints nothing)
from cogs.playback import Playback

PASS, FAIL = [], []


def ok(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("PASS" if cond else "FAIL") + f"  {label}")


def make_pb(state=None):
    """Playback instance WITHOUT running __init__ (no tasks started)."""
    pb = object.__new__(Playback)
    saves = []
    pb.bot = SimpleNamespace(
        guild_states={1: state} if state is not None else {},
        save_state=lambda: saves.append(1),
        get_guild=lambda gid: None,
        http_session=None,
        station_manager=SimpleNamespace(get_station=lambda name: None),
        loop=None,
    )
    pb._saves = saves
    return pb


# ---------------------------------------------------------------- I1: epoch staleness
base_state = {"should_play": True, "retries": 2, "play_epoch": 5,
              "now_playing_message_id": 777, "url": "http://x", "stream_name": "S"}

# stale, no error -> must not touch state at all
pb = make_pb(dict(base_state))
pb.after_playback_handler(1, play_epoch=4, error=None)
s = pb.bot.guild_states[1]
ok(s["should_play"] is True and s["retries"] == 2 and s["now_playing_message_id"] == 777
   and pb._saves == [],
   "I1 stale callback (no error) leaves state untouched, no save_state")

# stale WITH error -> must not bump retries or schedule anything
pb = make_pb(dict(base_state))
pb.after_playback_handler(1, play_epoch=4, error=RuntimeError("boom"))
s = pb.bot.guild_states[1]
ok(s["retries"] == 2 and s["should_play"] is True and pb._saves == [],
   "I1 stale callback (error) does not bump retries / schedule reconnect")

# no state at all -> quiet return
pb = make_pb(None)
pb.bot.guild_states = {}
try:
    pb.after_playback_handler(99, play_epoch=1, error=None)
    ok(True, "I1 missing state handled quietly")
except Exception as e:
    ok(False, f"I1 missing state raised: {e}")

# ---------------------------------------------------------------- I7: single-flight reconnect
async def test_single_flight():
    pb = make_pb({"should_play": True, "retries": 0})
    await pb._schedule_reconnect(1)
    t1 = pb.bot.guild_states[1].get("reconnect_task")
    await pb._schedule_reconnect(1)
    t2 = pb.bot.guild_states[1].get("reconnect_task")
    await asyncio.sleep(0.05)  # let the cancellation actually process on the loop
    ok(t1 is not None and t1.cancelled(), "I7 first reconnect task cancelled by second schedule")
    ok(t2 is not None and t2 is not t1 and not t2.done(), "I7 second task stored, strong reference held")
    # should_play=False refuses to schedule
    t2.cancel()
    pb.bot.guild_states[1]["should_play"] = False
    await pb._schedule_reconnect(1)
    ok(pb.bot.guild_states[1].get("reconnect_task") is None or
       pb.bot.guild_states[1]["reconnect_task"] is t2,
       "I7 no new task scheduled when should_play=False")
    # cleanup pending tasks
    for k in ("reconnect_task",):
        task = pb.bot.guild_states[1].get(k)
        if task and not task.done():
            task.cancel()
    await asyncio.sleep(0)
    # drain cancellations
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

asyncio.run(test_single_flight())

# ---------------------------------------------------------------- I6: probe guards (no network)
pb = make_pb({"should_play": False})
r = asyncio.run(pb._probe_stream(1, "http://example.com/stream"))
ok(r is None, "I6 probe skips (returns healthy-unknown) when http_session is None")

pb = make_pb({"should_play": False})
pb.bot.http_session = SimpleNamespace(closed=True)
r = asyncio.run(pb._probe_stream(1, "http://example.com/stream"))
ok(r is None, "I6 probe skips when http_session is closed")

# ---------------------------------------------------------------- _play_internal invalid state
pb = make_pb(None)
pb.bot.guild_states = {}
started = asyncio.run(pb._play_internal.__wrapped__(pb, 1, None)) if hasattr(pb._play_internal, "__wrapped__") else None
# _play_internal is a plain coroutine method (not loop-wrapped) -> call directly
pb = make_pb(None)
pb.bot.guild_states = {}
started = asyncio.run(pb._play_internal(1, None))
ok(started is False, "_play_internal returns False with no state")

pb = make_pb({"should_play": False, "url": "http://x"})
started = asyncio.run(pb._play_internal(1, None))
ok(started is False and pb.bot.guild_states[1]["should_play"] is False,
   "_play_internal returns False when should_play is False")

# ---------------------------------------------------------------- I8/I5c: watchdog give-up path
class FakeVC:
    def __init__(self):
        self.disconnected = None
    def is_connected(self): return True
    def is_playing(self): return False
    def is_paused(self): return False
    async def disconnect(self, force=False): self.disconnected = force

class FakeChannel(discord.TextChannel):
    """Subclasses the real TextChannel so production isinstance guards accept it."""
    def __init__(self):
        self.sent = []          # skip discord.py __init__ entirely; we only override what's used
    async def send(self, msg): self.sent.append(msg); return SimpleNamespace(id=1)

class FakeText(FakeChannel):
    pass

class FakeGuild:
    def __init__(self, vc, channel):
        self.voice_client = vc
        self._ch = channel
    def get_channel(self, cid): return self._ch

async def test_watchdog_giveup():
    vc, ch = FakeVC(), FakeText()
    guild = FakeGuild(vc, ch)
    import time as _t
    state = {
        "should_play": True, "retries": config.MAX_RECONNECT_ATTEMPTS,  # already exhausted
        "starting_at": None, "last_playing_at": _t.monotonic() - 999,
        "stream_name": "Dead Station", "text_channel_id": 55, "url": "http://dead",
        "now_playing_message_id": None, "play_epoch": 1,
    }
    pb = make_pb(state)
    pb.bot.get_guild = lambda gid: guild
    coro_fn = getattr(pb.watchdog_loop, "_coro", None) or getattr(pb.watchdog_loop, "coro", None)
    if coro_fn is None:
        print("SKIP  I8 watchdog give-up (cannot access loop coroutine on this discord.py)")
        return
    await coro_fn(pb)
    s = pb.bot.guild_states[1]
    ok(s["should_play"] is False, "I8 watchdog give-up clears should_play")
    ok(vc.disconnected is True, "I8 watchdog give-up disconnects voice")
    ok(len(ch.sent) == 1 and "Dead Station" in ch.sent[0] and "Lost the stream" in ch.sent[0],
       f"I8 watchdog give-up posts one channel notice: {ch.sent[:1]}")
    ok(pb._saves != [], "I8 watchdog give-up saves state")

asyncio.run(test_watchdog_giveup())

# ---------------------------------------------------------------- probe-before-join placement (static)
import inspect
src = inspect.getsource(Playback._play_command_logic)
ok("_probe_stream" in src and src.index("_probe_stream") < src.index("ensure_voice_and_play"),
   "I6 probe runs BEFORE ensure_voice_and_play in play flow")
ok("('http://', 'https://')" in inspect.getsource(Playback._play_command_logic),
   "I10 URL gate uses https:// with trailing slashes")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
