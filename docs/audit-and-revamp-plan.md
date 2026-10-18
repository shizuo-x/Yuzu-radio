# Yuzu Radio Bot — Audit & Revamp Plan (feature/yuzu-revamp)

Date: 2026-10-18 · Auditors: 3× GLM-5.3 subagents (playback/list, AI layer, industry research) + orchestrator first-hand review · Verified against actual code, not plans.

---

# PART 1 — DIAGNOSIS (merged, prioritized)

## 1.1 Your three complaints, root-caused

### A. "The 250+ station list is hard to find/understand/play"
- **`/list` is still pure reaction-paging** (◀️/▶️ `wait_for` loop) — no select menu, no buttons, no `discord.ui.View` anywhere. 10/page → **27 pages** at 269 stations, 26 clicks to reach the end (`cogs/utility.py:177-209`).
- The `.kilo` plan's button+dropdown redesign (2.1) was **written but never executed** — all 7 audited kilo items are PENDING (1.4, 1.5, 1.6, 1.12, 2.1, 2.2, 2.3).
- Page content hard-truncated at 1020 chars → last stations on a page silently invisible (`utility.py:173`).
- After 120 s the loop exits via bare `except: break` → reactions stay but do nothing (`:209`).
- Station order = PocketBase `-created` refetched **on every /list** → `/play <n>` numbering shifts when any station is added (`core/stations.py:30`, `utility.py:183-186`).
- No search-in-UI, no country/tag scoping — country/tags are flattened into a display-only `desc` string (`stations.py:63-68`).

### B. "NL→tool calling sucks; search by tag/country/name only sometimes works"
- Good news: all 3 backends DO use **native** function calling (`deepseek.py:129-146`, `local.py:131-148`, `gemini.py:108-128`) — not text scraping.
- **But every backend allows exactly ONE tool round** (`deepseek.py:146-186`, `local.py:148-186`, `gemini.py:119-146`). No loop exists — the kilo plan explicitly declared multi-round "out of scope".
  - Consequence: search→play in one turn is structurally impossible; if the model tries a 2nd tool call, `message['content']` is `None` → `None.strip()` `AttributeError` (`deepseek.py:186`) → swallowed → user gets *"I'm having trouble thinking right now."*
- **Shipped config is tool-hostile**: `.env.example` sets `AI_PROVIDER=deepseek` + `DEEPSEEK_MODEL=deepseek-reasoner` (a non-function-calling model) while `config.py:36` defaults to `gemini` — mismatch (kilo 3.3, pending).
- **Nickname mentions ignored**: `startswith(bot.user.mention)` misses `<@!id>` (kilo 1.9, pending — `ai_assistant.py:233`).
- **`search_radio` reads fields that don't exist**: `data['country']`/`data['tags']` always empty because `StationManager` flattens them into `desc` (kilo 1.10, pending). Search = substring on concatenated string → multi-word/tag queries over- or under-match. This is why tag/country search "kinda works sometimes".
- History stores only final text pairs; tool results never persisted; the `error executing` transient pattern erases even useful turns (`ai_assistant.py:24-35, 261-266`) → bare "yes"/"the first one" confirmations rely on the model's memory of its own prose.
- Gemini backend: only `parts[0]` inspected (drops text+call orderings), system prompt concatenated into the user turn, no `system_instruction`.

### C. "It joins voice but never starts the stream"
Five stacked causes, all silent to the user:
1. **CRITICAL — false success**: `_play_internal` swallows start failures (`playback.py:136-140`, comment `# Optionally notify text channel here` — never implemented) and `ensure_voice_and_play` returns `"▶️ Now playing: …"` **unconditionally** (`:244`). AI relays success; bot is joined + silent + `should_play=False` (no retry will ever fire).
2. **CRITICAL — station-switch race**: `ensure_voice_and_play` sets `should_play=True` (`:220-226`) *before* `stop()` of the old source (`:123`); the old source's `after` callback (error=None) then forces `should_play=False` (`:180-183`) — killing all future recovery, metadata updates, and embeds while audio may actually be playing.
3. **No watchdog**: a stream that connects but sends no bytes keeps FFmpeg alive → `after` never fires → no error, no retry, indefinite silence (`:125-127`). Dead URLs are common (~14% measured in your PocketBase before cleanup).
4. **Reconnect stampede**: 3 uncoordinated schedulers (`:284`, `:169`, `:520`), no single-flight, `retries` reset in two paths → overlapping stop/play cycles, `ClientException('Already playing')`, unbounded retries (kilo 1.4, pending).
5. Give-up after 3 retries is log-only: no channel notification, bot stays parked in VC (`:173-176`). Plus stale `state['vc']` breaking the metadata loop (kilo 1.5) and `fetch_user` REST per 30 s (kilo 1.6).

## 1.2 What industry leaders do (research summary)
- **Funnel, not pages**: autocomplete on `/play` (25 choices, 3 s hard window, **ID-valued**, name ≤100 chars) → scoped browse via **select menus (25 options each)** → result **picker buttons/select** for ambiguous queries. Nobody reaction-paginates catalogs; FredBoat replaced its numbered-list flow with selectable buttons; RADBot (30k stations) does category drill-down + cross-field search + shuffle; Padio uses autocomplete + a persistent control-panel message.
- **Reliable play order**: defer → voice permission pre-flight (connect+speak) → **probe the stream BEFORE joining** (2 s ffmpeg/HEAD check; kills most join-but-silent reports) → atomic connect/move (timeout-wrapped, `self_deaf=True`) → `after` callback logs errors → **wait ~2-3 s and verify `is_playing()` before announcing** → per-guild watchdog: silent beyond grace → refresh URL → retry once → notify + leave.
- **LLM tool calling**: native function calling + **validation gateway** (Pydantic) + **ID-reference protocol** (search returns IDs; play accepts only IDs returned this session; invalid → structured error fed back to the model, ≤2 repair retries) + **bounded loop (≤6 rounds)** + UI-side disambiguation (select menu of candidates) + the SAME play pipeline for slash and AI callers.
- Hard limits: autocomplete 25 choices / 3 s / no defer / suggestions not enforced; select 25 options (label 100, desc 100); 5 action rows; 25 buttons; embed field 1024, 25 fields, 6000 total; interaction ack 3 s (defer→15 min).

---

# PART 2 — TARGET ARCHITECTURE ("the ultimate bot")

## A. Station data layer (`core/stations.py`)
1. Store `country`, `tags[]`, `image_url`, `is_featured` as **first-class fields** (keep `desc` for display). Loaders: PocketBase + JSON.
2. **Stable alphabetical sort** (lowercase name) after every load → `/play <n>` numbering stable (kilo 2.3).
3. **TTL cache + background refresh** (fetch once at startup, refresh every 5 min); `/list`/autocomplete/AI never do live PB calls on interaction paths; surface fetch errors instead of silently degrading to the 1-station JSON.
4. `search_stations(query=None, country=None, tag=None, limit=…)` — tokenized scoring: exact name > prefix > substring > tag > country; returns structured dicts **including PocketBase record id** (stable station_id for autocomplete values + AI protocol).
5. Raise/remove the `perPage=500` cliff (paginate).

## B. Playback reliability (`cogs/playback.py`)
1. **Switch race fix**: stop old source *before* latching new intent, and give the after-handler a per-guild epoch/token — stale callbacks skip the state write.
2. **Verified start**: `_play_internal` returns outcome; failures → message in the text channel; `ensure_voice_and_play` confirms `is_playing()` after a ~2-3 s grace before returning "Now playing" (or a real error). No more false success.
3. **Pre-flight stream probe** (HEAD/2 s read, aiohttp) before joining → "station offline" without ever connecting.
4. **Single-flight reconnect** per guild (cancel-and-replace task handle); `retries` reset ONLY on successful start (kilo 1.4).
5. **Watchdog** per guild: connected + not playing > grace → refresh URL from station layer → one retry → notify + disconnect.
6. Remove `state['vc']`; resolve `guild.voice_client` live (kilo 1.5). Give-up → notify channel + leave.
7. `fetch_user` → mention string (1.6); URL gate `('http://', 'https://')` (1.12); narrow bare excepts.
8. Startup check: opus/PyNaCl/ffmpeg present, fail loudly.

## C. Browse & list UX (`cogs/utility.py` + play command)
1. **`/play station:<autocomplete>`** — primary path: local-cache scoring, top 25, `value=station_id`, name shows `name · country · tags` (≤100 chars). Free-text fallback → fuzzy → **disambiguation picker** (25-option select) when ambiguous.
2. **`/stations`** (replaces the 27-page reaction `/list`): View with row 1 = category select (countries/genres top-24 + All), row 2 = station select (25/page, label=name, desc=country·tags), row 3 = `◀ Prev · Page x/y · Next ▶ · Shuffle · Cancel`. Author-locked, `timeout=180`, `on_timeout` disables children; slash = ephemeral, prefix = public; select **plays instantly** via the shared play pipeline (permission pre-flight + verification). No per-call PB fetch (cache).
3. `/help` → same button pattern (kilo 2.2).
4. Optional later: persistent `/setup panel` (Padio pattern).

## D. AI layer (`cogs/ai_assistant.py` + `ai_backends/`)
1. **Bounded multi-round tool loop (≤6 rounds)** in every backend: execute → append results → re-request; `content is None` guard; chained calls legal. (Re-scopes the kilo "out of scope" item — it IS the #1 complaint.)
2. **Refactor**: one shared OpenAI-style loop for deepseek/local (dedupe); gemini iterates **all parts/calls**, passes `system_instruction=` properly.
3. **Validation gateway**: Pydantic-style arg models per tool + tool-name allowlist; invalid → structured error returned to the model for repair (≤2), never a dead-end string.
4. **ID-reference protocol**: `search_stations` returns `station_id` + name/country/tags; `play_station(station_id | exact_name)`; bare "yes"/"3" resolves against a **session candidate store** (last search results per channel).
5. **Same play pipeline as slash**: probe → permission pre-flight → verified start; AI gets real success/failure text.
6. Expanded toolset: `search_stations(query?, country?, tag?, limit)` (structured), `play_station`, `stop_radio`, `leave_voice`, `now_playing`, `browse_stations?` + existing reminder tools.
7. Mention gate: `re.match(rf"^<@!?{id}>\s*")` (1.9); lazy gemini import (1.8); history LRU ~50 channels (2.5); persist compact tool-result summaries in history; drop the over-broad `error executing` transient pattern.
8. Config: `.env.example` aligned (`AI_PROVIDER=gemini` or `deepseek` + `DEEPSEEK_MODEL=deepseek-chat` — never `deepseek-reasoner`); document tool-capable defaults.

## E. Hygiene
Bare excepts (`utility.py`, `playback.py`, `core/bot.py`), `dev_tools` genai guard + `asyncio.to_thread`, backends `__init__.py`, docker-compose version line, `/now` sentinel cleanup.

---

# PART 3 — PHASING, EXECUTION & VERIFICATION

| Wave | Phase | Files | Depends |
|---|---|---|---|
| A (parallel) | P1 Playback reliability | `cogs/playback.py` | — |
| A (parallel) | P2 Station data layer | `core/stations.py` | — |
| B (parallel) | P3 Browse/list UX | `cogs/utility.py` (+play cmd wiring) | P2 API |
| B (parallel) | P4 AI revamp | `cogs/ai_assistant.py`, `cogs/ai_backends/*` | P2 API + P1 pipeline |
| C (me) | P5 Hygiene + tests + review | scattered | all |

Rules for implementation agents: edit ONLY their assigned files (shared constants live in their own file — no `config.py` collisions); never run the bot; `python -m py_compile` every touched file; branch `feature/yuzu-revamp` only.

Verification (orchestrator):
1. `py_compile` all touched files + import smoke test (deps permitting).
2. **Unit tests (new, LLM-free)**: tool-loop harness with mocked backend responses (tool_calls sequences → assert search→play chains in one turn, None-content guard, repair path); station search scoring (name/country/tag, sorting, dedup); autocomplete choice shaping (25 cap, 100-char names, station_id values).
3. `git diff` review of every hunk against this doc.
4. Manual checklist for the user's test server: `/play` autocomplete → select → verified now-playing; `/stations` category → select → plays; ambiguous search → picker; `@Bot play some malaysian station` → search+play same turn; kill a stream URL → error message + retry + notification; nickname-mention works; `/play 5` stable across station additions.

Appendix: full auditor reports (see branch history / chat transcript of 2026-10-18).
