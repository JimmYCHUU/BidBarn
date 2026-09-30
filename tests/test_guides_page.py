"""The guide pages: the list of model codes, and the whole guide behind one code.

The routes are registered on the app the way `dashboard/routes.py` will do it, with a shell helper
that mirrors `review/app.py::page`. Everything runs on the offline fixture config (the GDH206 guide
from `tests/fixtures/guide_223_tree.json`) in a temporary folder.
"""
import copy
import json
import re

from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

from auction_bot.carbarn.client import append_override
from auction_bot.carbarn.models import parse_guide_tree
from auction_bot.dashboard import guides
from auction_bot.dashboard import status as st
from auction_bot.dashboard.jobs import Busy, DisabledJobManager, NotRunning, build_argv
from auction_bot.judge.playbook import append_playbook
from auction_bot.review.app import TEMPLATES, create_app, icon, lot_title, status_label
from auction_bot.store import Store
from tests.test_pipeline_offline import FIX, cfg  # noqa: F401  (fixture)
from tests.test_triage import _seed


class FakeJobs(DisabledJobManager):
    """Reads runs like the real manager; `start` checks the options for real and records the run, no process."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.live: int | None = None
        self.cancelled: list[int] = []

    def current(self):
        if self.live is None:
            return None
        r = self.get(self.live)
        return {"id": r["id"], "command": r["command"], "args": r["args"], "started_at": r["started_at"],
                "progress": r["progress"], "cancel_requested": False}

    def start(self, command, options=None):
        argv = build_argv(command, options, self.commands)
        if self.live is not None:
            raise Busy(f"{self.get(self.live)['command']} is running (run {self.live})", run_id=self.live)
        s = Store(self.cfg.db_path)
        try:
            self.live = s.start_run(command, argv)
        finally:
            s.close()
        return self.get(self.live)

    def cancel(self, run_id):
        if run_id != self.live:
            raise NotRunning(f"run {run_id} is not running")
        self.cancelled.append(run_id)


class _Never:
    """`can_finish` for the shell: false whether the template tests it as a value or calls it."""

    def __bool__(self):
        return False

    def __call__(self):
        return False


def _client(cfg) -> TestClient:
    """The whole app, with the guide routes registered on it the way routes.py will, over a shell
    helper that mirrors review/app.py::page (counts for the nav, the lock for the header)."""
    app = create_app(cfg, jobs=FakeJobs(cfg))
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.globals.update(icon=icon, status_label=status_label, lot_title=lot_title, asset_v="test", can_finish=_Never())

    def store() -> Store:
        s = Store(cfg.db_path)
        s.cfg = cfg
        return s

    def page(request, name, nav, **context):
        if "counts" not in context:
            s = store()
            try:
                context["counts"] = s.counts()
            finally:
                s.close()
        context.update(nav=nav, lock_label=st.lock_label(cfg), dry_run=cfg.dry_run)
        return templates.TemplateResponse(request, name, context)

    guides.register(app, templates, cfg, store, page)
    return TestClient(app)


def _decide(client, pid, **data):
    r = client.post(f"/p/{pid}/decide", data=dict(data, via="triage"), headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text


def _row(page: str, year: int) -> list[str]:
    """The cells of one year of the first grid on the page, as text."""
    m = re.search(rf'<tr data-year="{year}"[^>]*>(.*?)</tr>', page, re.S)
    assert m, f"no row for {year}"
    return [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)]


def test_the_index_lists_every_code_with_its_counts_and_the_snapshot_age(cfg):
    store = Store(cfg.db_path)
    _seed(store, "A", "GDH206", "4", 90_000)
    _seed(store, "B", "GDH206", "4", 60_000, status="SKIP")
    _seed(store, "Z", "ZZZ99", "4", 10_000)  # a code the newest snapshot does not have
    store.close()
    client = _client(cfg)
    r = client.get("/guides")
    assert r.status_code == 200
    page = r.text
    assert "<h1 class=\"page-title\">Guides</h1>" in page
    assert "1 guide in the snapshot, fetched just now" in page
    assert 'href="/guides/GDH206"' in page and "Toyota Hiace" in page and "Active" in page
    assert re.search(r'href="/review\?model=GDH206"><span class="count count--hot">1</span>', page)
    assert "ZZZ99" in page and "not in the newest guides" in page and 'href="/guides/ZZZ99"' not in page
    assert "No guides on this PC" not in page

    # A stored code the snapshot lost is not a page; a code in the snapshot is, whatever its case.
    assert client.get("/guides/ZZZ99").status_code == 404
    assert client.get("/guides/gdh206").status_code == 200

    # No snapshot at all: the empty state, and no guide page.
    cfg.snapshot_dir = cfg.snapshot_dir.parent / "no-snapshots"
    cfg.snapshot_dir.mkdir()
    page = client.get("/guides").text
    assert "No guides on this PC" in page and "Fetch the guides from the Runs page, or start Bid mode." in page
    assert client.get("/guides/GDH206").status_code == 404


def test_the_guide_page_shows_the_grid_the_modifiers_and_the_standing_instructions(cfg):
    # The fixture's file has one free line; add a skip line the bot understands and one it does not.
    append_override(cfg, "GDH206", "skip: variant contains DUMP", author="review")
    append_override(cfg, "GDH206", "skip: seats >= many", author="review")
    client = _client(cfg)
    r = client.get("/guides/GDH206")
    assert r.status_code == 200
    page = r.text

    # The head.
    assert re.search(r'<h1 class="page-title">Toyota Hiace <span class="mono muted">GDH206</span></h1>', page)
    assert "Grade 3.5 and up · 2018 to 2024 · up to 160,000 km" in page
    assert "Active</span>" in page and 'href="/list?model=GDH206"' in page
    assert "Review " not in page.split('class="cols')[0].split('page-head')[1]  # nothing to review: no Review button

    # The requirements, as text rules.
    for t in ("No sticker paint", "2, 3, 5 or 6 seats", "4WD, long or super long wheelbase with 2, 3, 5 or 6 seats"):
        assert t in page, t
    assert "Chips you judge" not in page  # this guide has no chip

    # The grid: both variants, years down, bands across; 2020 / 120K-130K = 1,160 on GDH206V.
    assert "GDH206V" in page and "GDH206K" in page and page.count("Bid grid") == 2
    first = page.split("GDH206K")[0]
    head = re.search(r"<thead><tr><th class=\"num\">Year</th>(.*?)</tr></thead>", first, re.S).group(1)
    bands = re.findall(r'<th class="num">(.*?)</th>', head)
    assert bands[0] == "0K-10K" and bands[-1] == "120K-130K" and len(bands) == 13
    row = _row(first, 2020)
    assert row[0] == "2020" and row[bands.index("120K-130K") + 1] == "1,160" and row[1] == "1,840"
    assert [c[0] for c in map(lambda y: _row(first, y), range(2018, 2025))] == ["2018", "2019", "2020", "2021", "2022", "2023", "2024"]
    assert "no bid" not in first.split("<tbody>")[1].split("</tbody>")[0]  # every cell of GDH206V has a value
    # The grade bonus is applied by the bot and worded the way Jim reads it (grade 4 = 4 and above); the years the
    # dashboard switched off are one line, not fourteen.
    assert "Modifiers the bot applies: " in first
    assert "+80K for grade 4 and up, years 2018-2021" in first
    assert "no bid for 2004-2017 (rows off)" in first and "Row off" not in first
    assert "Chips" not in first.split("Bid grid")[1]

    # The standing instructions, with their stamps, read as the bot reads them.
    assert "Skip ambulance conversions" in page and "2026-09-14, jim" in page and "shown on every lot" in page
    assert "skip: variant contains DUMP" in page and "skips a lot when variant contains DUMP" in page
    assert "skip: seats &gt;= many" in page and "not understood, so never applied" in page and "is not a number for seats" in page
    # The Add form posts to the review page's own route and comes back here.
    assert re.search(r'<form method="post" action="/rules/GDH206/skip" class="row">\s*<input type="hidden" name="next" value="/guides/GDH206">', page)
    assert 'placeholder="skip: variant contains DUMP"' in page and ">Add</button>" in page
    assert "Nothing is stored until you add it. A new rule applies at the next scan; Replay on the Runs page applies it to the stored lots." in page
    assert 'id="suggested"' in page and "Skip rules your rejections justify" not in page

    # A line added through that route shows on the next load without a new snapshot.
    r = client.post("/rules/GDH206/skip", data={"line": "km >= 150000", "next": "/guides/GDH206"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/guides/GDH206"
    page = client.get("/guides/GDH206").text
    assert "skip: km &gt;= 150000" in page and "skips a lot when km &gt;= 150000" in page

    # Nothing decided, nothing noted.
    assert "No decisions on this model yet." in page and "0 to review" in page and "0 approved" in page
    assert "Nothing yet. Your review corrections and learn-mode comments on this model appear here." in page


def test_notes_suggestions_and_decisions(cfg):
    store = Store(cfg.db_path)
    rejected = [_seed(store, ref, "GDH206", "4", km) for ref, km in (("R0", 50_000), ("R1", 51_000))]
    for pid in rejected:
        store.conn.execute("UPDATE proposals SET facts_json=json_set(facts_json, '$.seats', 10) WHERE id=?", (pid,))
    store.conn.commit()
    approved = _seed(store, "K", "GDH206", "4.5", 70_000)
    _seed(store, "P", "GDH206", "4", 80_000)  # stays pending
    _seed(store, "S", "GDH206", "4", 20_000, status="SKIP")
    # A learn-mode comment on a lot of this model, and a review correction in the playbook.
    store.upsert_vehicle("N1", "GDH206", {"ref_no": "N1", "year": 2019, "variant_text": "Van DX", "km": 88_000, "grade_raw": "4"})
    store.add_manual_actions("session-1", [("N1", "reason", None, None, "2026-09-25T10:00:00", "too many seats for a camper")])
    store.close()
    append_playbook(cfg, "GDH206", "Beige seats are not dark", author="review")

    client = _client(cfg)
    for pid in rejected:
        _decide(client, pid, action="reject", reason="10 seats on the sheet")
    _decide(client, approved, action="approve", amount="1000")

    page = client.get("/guides/GDH206").text
    # The head knows what waits, and the Lots card counts everything stored under the code.
    assert re.search(r'<a class="btn btn--primary" href="/review\?model=GDH206">.*?Review 1</a>', page, re.S)
    assert "1 to review" in page and "1 approved" in page and "2 rejected" in page and "1 skipped" in page
    assert 'href="/list?model=GDH206&amp;status=SKIP"' in page and "5 lots stored" in page

    # The two rejections justify one skip line; its Add form posts to the review page's route and comes back here.
    block = page.split('id="suggested"')[1].split("</section>")[0]
    assert "Skip rules your rejections justify" in block and "skip: seats = 10" in block and "(2 rejections)" in block
    assert re.search(r'<form method="post" action="/rules/GDH206/skip">\s*<input type="hidden" name="line" value="skip: seats = 10">'
                     r'<input type="hidden" name="next" value="/guides/GDH206">', block)
    assert block.count(">Add</button>") == 1

    # The comments: the learn-mode note with its lot in brief, and the playbook line with its stamp.
    assert "too many seats for a camper" in page and "[2019 Van DX, 88,000 km, grade 4]" in page and "learn mode" in page
    assert "Beige seats are not dark" in page and re.search(r"\d{4}-\d{2}-\d{2}, review", page)

    # The decisions, newest first, each linking its lot.
    table = page.split("Recent decisions")[1].split("</table>")[0]
    assert table.count("<tr>") == 3 + 1  # three decisions and the head row
    assert 'href="/p/%d"' % approved in table and "Approved" in table and "<b>1,000</b>K" in table
    assert table.count("Rejected") == 2 and table.count("10 seats on the sheet") == 2
    assert table.index('href="/p/%d"' % approved) < table.index('href="/p/%d"' % rejected[1])


def test_unknown_or_hostile_codes_are_not_found_and_lower_case_works(cfg):
    client = _client(cfg)
    for path in ("/guides/NOPE", "/guides/..%2Fx", "/guides/GDH206%20--x", "/guides/GDH206;drop"):
        assert client.get(path).status_code == 404, path
    r = client.get("/guides/gdh206")
    assert r.status_code == 200 and 'action="/rules/GDH206/skip"' in r.text and "/guides/GDH206" in r.text


def test_a_code_shared_by_two_guides_shows_both(cfg):
    snap = cfg.snapshot_dir / "rules_test.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    second = copy.deepcopy(data["guides"][0])
    second["tree"]["id"] = second["item"]["id"] = 9999
    second["tree"]["model"] = second["item"]["model"] = "Hiace Campervan"
    second["tree"]["notes"] = "Internal campervan fit out"
    data["guides"].append(second)
    snap.write_text(json.dumps(data), encoding="utf-8")

    client = _client(cfg)
    page = client.get("/guides/GDH206").text
    assert "Toyota Hiace <span" in page and "Toyota Hiace Campervan" in page and "2 guides share this code" in page
    assert page.count("Requirements</h2>") == 2 and page.count("Bid grid</h2>") == 4 and page.count('data-year="2020"') == 4
    assert "Internal campervan fit out" in page and "guide 9999" in page
    assert page.count("Standing instructions</h2>") == 1 and page.count("Recent decisions</h2>") == 1  # per code, not per guide

    index = client.get("/guides").text
    assert "2 guides in the snapshot" in index and "Toyota Hiace, Toyota Hiace Campervan" in index
    assert index.count('href="/guides/GDH206"') == 1


def test_guide_view_tells_chips_from_the_modifiers_the_bot_applies():
    tree = {
        "id": 1, "make": "Nissan", "model": "Serena", "modelCode": "GC27", "minGrade": 4, "active": True,
        "updatedAt": "2026-09-01T00:00:00", "notes": "Dark seats|Pro Pilot present",
        "variants": [{"id": 10, "name": "", "active": True,
                      "bandTemplate": [{"odoFrom": 0, "odoTo": 50000}, {"odoFrom": 50000, "odoTo": 100000}],
                      "blocks": [{"selected": True, "years": [
                          {"year": 2019, "eligible": True, "lines": [
                              {"odoFrom": 0, "odoTo": 50000, "biddingJpyK": 910}, {"odoFrom": 50000, "odoTo": 100000, "biddingJpyK": 780}]},
                          {"year": 2018, "eligible": False, "rowNoBid": True, "lines": [
                              {"odoFrom": 0, "odoTo": 50000, "biddingJpyK": 860},
                              {"odoFrom": 50000, "odoTo": 100000, "biddingJpyK": None, "effectiveNoBid": True, "effectiveReason": "over the odo limit"}]}]}]}],
        "modifiers": [
            {"id": 1, "variantId": None, "conditions": [{"attr": "GRADE", "op": "eq", "value": "4.5"}], "amountJpyK": 90, "active": True},
            {"id": 2, "variantId": None, "conditions": [], "amountJpyK": -220, "note": "No Pro Pilot", "active": True},
            {"id": 3, "variantId": None, "conditions": [], "amountJpyK": 170, "note": "Autech", "active": False},
            {"id": 4, "variantId": None, "conditions": [], "percent": 92, "note": "Bid at 92%", "active": True},
            {"id": 5, "variantId": None, "conditions": [{"attr": "SEATS", "op": "eq", "value": "8"}], "noBid": True, "active": True, "stale": True},
            {"id": 6, "variantId": None, "conditions": [{"attr": "COLOUR", "op": "in", "values": ["white", "pearl"]}], "amountJpyK": 50, "active": True},
        ],
    }
    rule = parse_guide_tree(tree)
    rule.override_text = ("# Standing instructions for GC27\n\n- (2026-09-14, jim) Beige is not dark\n"
                          "- (2026-09-15, review) skip: seats = 7\n- (2026-09-16, review) skip: seats >= many\n- bid: seats = 8\n")
    g = guides.guide_view(rule)

    assert (g["name"], g["code"], g["guide_id"], g["active"], g["min_grade"]) == ("Nissan Serena", "GC27", 1, True, 4.0)
    assert g["years"] == (2019, 2019) and g["max_odo"] == 100000  # the search ranges come from the cells with a value
    assert g["text_rules"] == ["Dark seats", "Pro Pilot present"]

    [v] = g["variants"]
    assert v["label"] == "Default" and v["bands"] == ["0K-50K", "50K-100K"]
    assert [(r["year"], r["eligible"], r["biddable"]) for r in v["rows"]] == [(2018, False, False), (2019, True, True)]
    assert [c["k"] for c in v["rows"][1]["cells"]] == [910, 780] and not any(c["no_bid"] for c in v["rows"][1]["cells"])
    off = v["rows"][0]["cells"]
    assert all(c["no_bid"] for c in off) and off[0]["reason"] == "row off" and off[1]["reason"] == "over the odo limit"
    assert off[0]["k"] == 860  # the value is kept so the page can say what is switched off

    # The bot applies conditioned amounts; a grade bonus reads "and up"; a chip is Jim's; the dial is already in
    # the grid; inactive or stale ones are off.
    assert [m["text"] for m in v["applied"]] == ["+90K for grade 4.5 and up", "+50K, COLOUR in white, pearl"]
    assert [(m["note"], m["amount_k"]) for m in v["chips"]] == [("No Pro Pilot", -220)]
    assert [m["text"] for m in v["dials"]] == ["x92.0%, Bid at 92%"]
    assert [(m["id"], m["active"], m["stale"]) for m in v["off"]] == [(3, False, False), (5, True, True)]
    assert v["rows_off"] == ""
    assert g["chips"] == [{"label": "No Pro Pilot", "amount_k": -220, "text": "-220K, No Pro Pilot", "variants": ["Default"]}]

    ins = g["instructions"]
    assert ins["free"] == ["Beige is not dark", "bid: seats = 8"]  # only skip: is a verb; anything else is words
    assert ins["skip"] == [{"raw": "skip: seats = 7", "conditions": ["seats = 7"], "needs_detail": False}]
    assert len(ins["errors"]) == 1 and "is not a number for seats" in ins["errors"][0]
    assert [(l["kind"], l["stamp"]) for l in ins["lines"]] == [("free", "2026-09-14, jim"), ("skip", "2026-09-15, review"),
                                                              ("error", "2026-09-16, review"), ("free", "")]
    assert ins["lines"][1]["conditions"] == ["seats = 7"] and "many" in ins["lines"][2]["error"]


def test_year_ranges_fold_the_rows_switched_off():
    assert guides._year_ranges([2004, 2005, 2006, 2010, 2012, 2013]) == "2004-2006, 2010, 2012-2013"
    assert guides._year_ranges([]) == "" and guides._year_ranges([2020]) == "2020"


def test_the_guide_module_spells_no_verb_it_may_not_use():
    """The page reads; the review page's own route writes the rule and only Jim's click decides."""
    from pathlib import Path
    source = Path(guides.__file__).read_text(encoding="utf-8")
    assert ".decide(" not in source and "append_override" not in source and "record_placement" not in source
    assert "--" + "live" not in source and "live" not in source.lower()
