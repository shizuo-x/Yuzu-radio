"""Independent orchestrator tests for core/stations.py (Yuzu revamp, P2).

Run:  python tests/test_stations.py    (no network, no bot, no .env reads)
Exit: 0 = all pass.
"""
import asyncio
import json
import os
import sys
import tempfile
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# --- Stub config BEFORE importing core.stations (avoids .env / dotenv entirely) ---
FIXTURE = [
    {"name": "Radio Rock", "stream_url": "http://a/rock", "country": "India",
     "tags": ["rock", "90s"], "image_url": "http://img/1", "is_featured": True, "id": "pb-rock"},
    {"name": "Classic Rock FM", "stream_url": "http://b/classicrock", "country": "USA",
     "tags": ["classic rock"], "image_url": "", "is_featured": False, "id": "pb-crock"},
    {"name": "Jazz Lounge", "stream_url": "http://c/jazz", "country": "France",
     "tags": ["jazz", "smooth jazz"], "image_url": "", "is_featured": False, "id": "pb-jazz"},
    {"name": "rock n roll radio", "stream_url": "http://d/rocknr", "country": "UK",
     "tags": ["rock"], "image_url": "", "is_featured": False, "id": "pb-rocknr"},
    {"name": "Zeta Radio", "stream_url": "http://e/zeta", "country": "Spain",
     "tags": ["pop"], "image_url": "", "is_featured": False, "id": "pb-zeta"},
]

tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
json.dump(FIXTURE, tmp)
tmp.close()

fake_config = types.ModuleType("config")
fake_config.POCKETBASE_URL = ""          # force JSON path (no network)
fake_config.STATIONS_FILE = tmp.name
fake_config.COMMAND_PREFIX = ","
fake_config.STOP_REACTION = "⏹️"
fake_config.DEFAULT_EMBED_COLOR = 0
sys.modules["config"] = fake_config

from core.stations import StationManager  # noqa: E402

PASS = []
FAIL = []


def ok(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("PASS" if cond else "FAIL") + f"  {label}")


class FakeBot:
    http_session = None


sm = StationManager(FakeBot())
asyncio.run(sm.fetch_stations())

# 1. Sorted + structure
names = list(sm.stations.keys())
ok(names == sorted(names, key=str.casefold), f"S2 alphabetical order: {names}")
data = sm.stations["Radio Rock"]
ok(all(k in data for k in ("name", "url", "country", "tags", "image_url", "is_featured", "desc", "station_id")),
   "S1 first-class keys present")
# JSON-fallback path: station_id is a stable slug of the name (spec: PB id only on PB records)
ok(data["station_id"] == "radio-rock", f"S1 JSON-path slug id: {data['station_id']}")
ok(isinstance(data["tags"], list) and data["tags"] == ["rock", "90s"], "S1 tags list")
ok("India" in data["desc"] and data["country"] == "India", "S1 desc + country")

# PB path: _process_pb_response must preserve the PocketBase record id
class FakeResp:
    status = 200
    async def json(self):
        return {"items": FIXTURE, "totalPages": 1}

pb_target = {}
pages = asyncio.run(sm._process_pb_response(FakeResp(), pb_target))
ok(pages == 1 and pb_target["Radio Rock"]["station_id"] == "pb-rock",
   "S1 PB path preserves record id")
ok(pb_target["Radio Rock"]["country"] == "India" and pb_target["Radio Rock"]["tags"] == ["rock", "90s"],
   "S1 PB path first-class fields")
sm_pb = StationManager(FakeBot())
sm_pb.stations = pb_target
sm_pb._rebuild_sorted()
ok(sm_pb.get_by_id("pb-jazz") is not None and sm_pb.get_by_id("pb-jazz")[0] == "Jazz Lounge",
   "get_by_id resolves PB ids")

# 2. Search ranking
r = sm.search_stations("radio rock", limit=5)
ok(r and r[0][0] == "Radio Rock", f"exact query ranks first -> {[n for n, _ in r]}")
r = sm.search_stations("rock", limit=5)
ok(len(r) >= 3 and all("rock" in (n + " " + " ".join(d["tags"])).lower() for n, d in r),
   "token query returns rock stations only")
# exact-beats-partial: "Radio Rock" (exact) should outrank "Classic Rock FM" for its own name
r = sm.search_stations("Radio Rock", limit=3)
ok(r[0][0] == "Radio Rock", "exact name outranks partials")

# 3. Filters (before scoring)
r = sm.search_stations(query=None, country="spain", limit=10)
ok([n for n, _ in r] == ["Zeta Radio"], f"country filter -> {[n for n, _ in r]}")
r = sm.search_stations(query="lounge", tag="jazz", limit=10)
ok(len(r) == 1 and r[0][0] == "Jazz Lounge", "tag filter + query intersection")
r = sm.search_stations(query=None, tag="rock", limit=10)
ok({n for n, _ in r} == {"Radio Rock", "Classic Rock FM", "rock n roll radio"},
   "tag-only filter set")

# 4. Browse mode (no query) = alphabetical page
r = sm.search_stations(None, limit=3)
ok([n for n, _ in r] == names[:3], "browse mode = first 3 alphabetical")

# 5. Compat call shape used by the old AI consumer
r = sm.search_stations("jazz", limit=5)
ok(isinstance(r, list) and all(isinstance(x, tuple) and len(x) == 2 for x in r),
   "compat search_stations(query, limit=5) -> list[(name, data)]")

# 6. get_by_id / get_all_ordered (JSON path -> slug ids)
ok(sm.get_by_id("jazz-lounge") is not None and sm.get_by_id("jazz-lounge")[0] == "Jazz Lounge",
   "get_by_id hit (slug id)")
ok(sm.get_by_id("pb-jazz") is None, "get_by_id miss (PB id not on JSON-loaded set)")
ok([n for n, _ in sm.get_all_ordered()] == names, "get_all_ordered matches sorted keys")

# 7. fuzzy index stability (1-based over sorted keys)
n1, d1 = sm.fuzzy_find_station("1")
ok(n1 == names[0], f"fuzzy index 1 -> {n1}")
n2, d2 = sm.fuzzy_find_station("Radio Rock")
ok(n2 == "Radio Rock", "fuzzy exact name")
n3, d3 = sm.fuzzy_find_station("zeta")
ok(n3 == "Zeta Radio", "fuzzy substring")
n4, d4 = sm.fuzzy_find_station("definitely-not-a-station")
ok(n4 is None and d4 is None, "fuzzy miss returns (None, None)")

# 8. get_station case-insensitive direct lookup
ok(sm.get_station("jazz lounge") is not None, "get_station case-insensitive")

# 9. Reload keeps sorted + fetch is single-flight safe on JSON path
asyncio.run(sm.fetch_stations())
ok(list(sm.stations.keys()) == names, "re-fetch stays sorted & identical")
ok(sm.last_fetch_ok is True and sm.last_fetch > 0, "fetch hygiene flags set")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
os.unlink(tmp.name)
sys.exit(1 if FAIL else 0)
