"""Independent orchestrator tests for cogs/ai_backends/openai_loop.py (Yuzu revamp, P4).

Scripted fake HTTP session — no network, no API keys.
Run:  python tests/test_ai_tool_loop.py    (exit 0 = all pass)
"""
import asyncio
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from cogs.ai_backends.openai_loop import run_openai_tool_loop, MAX_MODEL_ROUNDS
from cogs.ai_backends.tool_gateway import validate_tool_call, TOOL_ALLOWLIST

PASS, FAIL = [], []


def ok(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("PASS" if cond else "FAIL") + f"  {label}")


# ------------------------------------------------------------------ fakes
class FakeResponse:
    def __init__(self, payload, status=200):
        self._p, self.status = payload, status
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._p
    async def text(self): return json.dumps(self._p)


class FakeSession:
    def __init__(self, responses):
        self.queue = list(responses)
        self.calls = []
    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(json)
        if not self.queue:
            raise AssertionError("API called more times than scripted")
        item = self.queue.pop(0)
        return FakeResponse(*item) if isinstance(item, tuple) else FakeResponse(item)


def reply(content, tool_calls=None):
    return {"choices": [{"message": {"content": content, "tool_calls": tool_calls}}]}


def tc(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class Recorder:
    def __init__(self): self.calls = []
    async def __call__(self, name, args):
        self.calls.append((name, args))
        if name == "search_radio":
            return "1) Foo Radio [id:abc] - India"
        if name == "play_radio":
            return "Now playing Foo Radio"
        return "ok"


async def run(responses, executor, messages=None):
    sess = FakeSession(responses)
    msgs = messages if messages is not None else [{"role": "user", "content": "play some rock"}]
    out = await run_openai_tool_loop(
        http_session=sess, api_url="http://fake/v1/chat/completions",
        headers={}, model="test-model", messages=msgs,
        tool_executor=executor, provider_label="test")
    return out, sess, msgs


# ------------------------------------------------------------------ 1. one-turn search -> play chain
async def t_chain():
    rec = Recorder()
    responses = [
        reply(None, [tc("search_radio", {"query": "rock"}, "c1")]),
        reply(None, [tc("play_radio", {"station_id": "abc"}, "c2")]),
        reply("Playing Foo Radio for you!"),
    ]
    out, sess, msgs = await run(responses, rec)
    ok(out == "Playing Foo Radio for you!", "one-turn chain returns final text")
    ok([n for n, _ in rec.calls] == ["search_radio", "play_radio"],
       "executor ran search THEN play in ONE user turn (the core fix)")
    ok(len(sess.calls) == 3, f"exactly 3 API rounds ({len(sess.calls)})")
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    ok(len(tool_msgs) == 2 and all(m.get("tool_call_id") for m in tool_msgs),
       "tool results appended with tool_call_id (OpenAI protocol)")
    ok(all("tools" in sess.calls[i] for i in range(3)), "tools present in all chain rounds")

# ------------------------------------------------------------------ 2. None content guards
async def t_none_guards():
    rec = Recorder()
    out, _, _ = await run([reply(None), ], rec)
    ok(out == "", "None content WITHOUT tool_calls -> empty string, no AttributeError")
    out, _, _ = await run([reply(None, [tc("now_playing", {}, "c1")]), reply("Nothing playing.")], rec)
    ok(out == "Nothing playing.", "None content WITH tool_calls flows normally")

# ------------------------------------------------------------------ 3. gateway repair loop
async def t_repair():
    rec = Recorder()
    responses = [
        reply(None, [tc("search_radio", {"query": "rock", "limit": 99}, "c1")]),  # invalid limit
        reply(None, [tc("search_radio", {"query": "rock", "limit": 5}, "c2")]),   # repaired
        reply("Found 5 stations."),
    ]
    out, _, msgs = await run(responses, rec)
    ok(out == "Found 5 stations.", "repair flow completes with final text")
    tool_contents = [m["content"] for m in msgs if m.get("role") == "tool"]
    ok(any("limit" in c.lower() and "invalid" in c.lower() for c in tool_contents),
       "gateway error string was fed BACK to the model as tool result")
    ok([a.get("limit") for n, a in rec.calls if n == "search_radio"] == [5],
       "invalid call never reached the executor; repaired call did (limit=5)")

# unknown tool -> allowlist error, no executor call
async def t_unknown_tool():
    rec = Recorder()
    responses = [
        reply(None, [tc("drop_database", {}, "c1")]),
        reply("I only manage radio things."),
    ]
    out, _, msgs = await run(responses, rec)
    ok(rec.calls == [], "unknown tool never executed")
    ok(any("unknown tool" in m["content"].lower() for m in msgs if m.get("role") == "tool"),
       "allowlist error returned to model")

# ------------------------------------------------------------------ 4. round budget
async def t_budget():
    rec = Recorder()
    responses = [reply(None, [tc("now_playing", {}, f"c{i}")]) for i in range(MAX_MODEL_ROUNDS)]
    responses.append(reply("Summary of everything that happened."))
    out, sess, msgs = await run(responses, rec)
    ok(out == "Summary of everything that happened.", "budget exhaustion returns forced summary")
    ok(len(sess.calls) == MAX_MODEL_ROUNDS + 1,
       f"exactly {MAX_MODEL_ROUNDS}+1 API calls ({len(sess.calls)})")
    ok("tools" not in sess.calls[-1], "final summary call has NO tools")
    ok(any(m.get("content", "").startswith("Tool round budget") for m in msgs if m.get("role") == "user"),
       "summary nudge appended before final call")
    ok(len(rec.calls) == MAX_MODEL_ROUNDS, "all tool calls executed before the cap")

# ------------------------------------------------------------------ 5. HTTP error never raises
async def t_http_error():
    rec = Recorder()
    sess = FakeSession([({}, 500)])
    out = await run_openai_tool_loop(http_session=sess, api_url="u", headers={}, model="m",
                                     messages=[{"role": "user", "content": "hi"}],
                                     tool_executor=rec, provider_label="test")
    ok("error with my brain (500)" in out, f"HTTP 500 -> friendly error string, no raise: {out!r}")

# ------------------------------------------------------------------ 6. malformed args JSON
async def t_bad_json():
    rec = Recorder()
    bad = {"id": "c1", "type": "function",
           "function": {"name": "search_radio", "arguments": "{not json"}}
    responses = [reply(None, [bad]), reply("Let me try differently.")]
    out, _, msgs = await run(responses, rec)
    ok(rec.calls == [], "malformed JSON args never reach the executor")
    ok(any("invalid JSON" in m["content"] for m in msgs if m.get("role") == "tool"),
       "invalid-JSON error fed back to model")

# ------------------------------------------------------------------ 7. gateway unit checks
ok("search_radio" in TOOL_ALLOWLIST and "play_radio" in TOOL_ALLOWLIST, "gateway allowlist covers core tools")
ok(validate_tool_call("nope", {})[0] is False, "gateway rejects unknown tool name")
ok(validate_tool_call("search_radio", {"limit": "7"})[0] is True, "gateway coerces limit str->int")
ok(validate_tool_call("search_radio", {"limit": 99})[0] is False, "gateway enforces limit 1-10")
ok(validate_tool_call("delete_reminder", {"reminder_id": "42"}) == (True, {"reminder_id": 42}),
   "gateway coerces reminder_id str->int")
ok(validate_tool_call("set_reminder", {"message": "x", "due_at_utc_iso": "not-a-date"})[0] is False,
   "gateway validates ISO timestamps")

# ------------------------------------------------------------------ run all async tests
for fn in (t_chain, t_none_guards, t_repair, t_unknown_tool, t_budget, t_http_error, t_bad_json):
    asyncio.run(fn())

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
