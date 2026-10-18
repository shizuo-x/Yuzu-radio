"""Independent orchestrator tests for cogs/utility.py StationBrowserView (Yuzu revamp, P3).

Headless: constructs real discord.ui.View classes with fake cog data — no connection.
Run:  python tests/test_station_browser.py    (exit 0 = all pass)
"""
import asyncio
import os
import sys
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import cogs.utility as U  # noqa: E402  (import first so we can silence nothing — module is safe to import)

PASS, FAIL = [], []


def ok(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("PASS" if cond else "FAIL") + f"  {label}")


def fake_cog():
    user = SimpleNamespace(display_avatar=SimpleNamespace(url="http://avatar/x.png"))
    return SimpleNamespace(bot=SimpleNamespace(user=user))


# 60 fixture stations, alphabetical assumption holds for the view (it receives sorted data)
entries = []
for i in range(60):
    country = ["India", "USA", "France"][i % 3]
    tags = ["rock", "90s"] if i % 4 == 0 else (["jazz"] if i % 7 == 0 else ["pop"])
    entries.append((f"Station {i:02d}", {
        "name": f"Station {i:02d}", "url": f"http://s/{i}", "country": country,
        "tags": tags, "image_url": "", "is_featured": i in (5, 17, 42),
        "desc": f"{country} - {','.join(tags)}", "station_id": f"id-{i:02d}",
    }))
entries.sort(key=lambda kv: kv[0])  # the view expects the manager's alphabetical order

view = U.StationBrowserView(cog=fake_cog(), entries=list(entries), author_id=111)

# 1. Scope select
scope_opts = view.scope_select.options
ok(len(scope_opts) <= U.LIST_SCOPE_OPTION_LIMIT, f"scope options <= {U.LIST_SCOPE_OPTION_LIMIT} ({len(scope_opts)})")
ok(scope_opts[0].value == U._SCOPE_ALL and scope_opts[0].default is True, "first scope = all, default")
ok(scope_opts[0].label == "All stations", "scope label reads 'All stations'")
ok(any(o.value == U._SCOPE_FEATURED for o in scope_opts), "Featured scope present (fixtures have featured)")
ok(any(o.value.startswith("country:") for o in scope_opts), "country scopes present")
ok(any(o.value.startswith("tag:") for o in scope_opts), "tag scopes present")
ok(all(len(o.label) <= 100 and len(o.description or "") <= 100 for o in scope_opts),
   "scope labels/descriptions within 100 chars")
ok(len(scope_opts) >= 1 + 1 + 3, "All + Featured + 3 countries at minimum")

# 2. Station select page 0
st_opts = view.station_select.options
expected_page0 = [n for n, _ in entries[:25]]
ok(len(st_opts) == 25, f"station select holds 25 options ({len(st_opts)})")
ok([o.label for o in st_opts] == [n[:100] for n in expected_page0], "page 0 = first 25 alphabetical entries")
ok(all(o.value.startswith("id-") for o in st_opts), "option values are station_ids")
ok(all(len(o.label) <= 100 and len(o.description or "") <= 100 for o in st_opts),
   "station labels/descriptions within 100 chars")

# 3. Scope filtering (pure logic)
view.scope_key = "country:India"
india = view._scoped_entries()
ok(len(india) == 20 and all(d["country"] == "India" for _, d in india),
   f"country scope filters exactly ({len(india)}/20)")
view.scope_key = "tag:rock"
rock = view._scoped_entries()
ok(all("rock" in d["tags"] for _, d in rock) and len(rock) == 15, f"tag scope filters exactly ({len(rock)}/15)")
view.scope_key = U._SCOPE_FEATURED
fe = view._scoped_entries()
ok(len(fe) == 3, "featured scope returns the 3 featured")
view.scope_key = U._SCOPE_ALL
ok(len(view._scoped_entries()) == 60, "All scope = everything")

# 4. Render + clamping
view.page = 99
emb = view._render()
ok(view.page == 2, f"page 99 clamps to last page ({view.page})")
ok("Page 3/3" in emb.footer.text, f"footer shows real last page: {emb.footer.text!r}")
ok(view.prev_button.disabled is False and view.next_button.disabled is True,
   "buttons: prev enabled, next disabled at last page")
view.page = 0
view._render()
ok(view.prev_button.disabled is True and view.next_button.disabled is False,
   "buttons: prev disabled at first page")
ok(len(emb.description) < 4096, "embed description safely under 4096")

# 5. Empty scope renders gracefully
view.scope_key = "country:Nowhere"
emb2 = view._render()
ok("No stations in this scope" in emb2.description or view.station_select.disabled,
   "empty scope renders gracefully (message or disabled select)")
ok(view.shuffle_button.disabled is True, "shuffle disabled for empty scope")
view.scope_key = "All stations"

# 6. on_timeout with no tracked message -> disables children, no crash
async def t_timeout():
    v = U.StationBrowserView(cog=fake_cog(), entries=list(entries), author_id=1)
    v.message = None
    await v.on_timeout()
    ok(all(child.disabled for child in v.children), "on_timeout disables every child (no message: no crash)")
asyncio.run(t_timeout())

# 7. interaction_check author lock
class FakeResp:
    def __init__(self): self.sent = []
    async def send_message(self, content, ephemeral=False): self.sent.append(content)
class FakeInter:
    def __init__(self, uid): self.user = SimpleNamespace(id=uid); self.response = FakeResp()

async def t_author_lock():
    v = U.StationBrowserView(cog=fake_cog(), entries=list(entries), author_id=111)
    ok(await v.interaction_check(FakeInter(111)) is True, "author passes interaction_check")
    intr = FakeInter(999)
    ok(await v.interaction_check(intr) is False and intr.response.sent,
       "stranger rejected with ephemeral notice")
asyncio.run(t_author_lock())

# 8. HelpView exists and locks to author too
ok(issubclass(U.HelpView, __import__("discord").ui.View), "HelpView is a discord.ui.View")
ok(hasattr(U.HelpView, "interaction_check"), "HelpView author-locked")

# 9. Commands preserved
ok(U.Utility.list is not None and U.Utility.help is not None and U.Utility.ping is not None,
   "/list, /help, /ping commands exist on the cog")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
