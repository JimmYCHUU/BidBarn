"""The 50-sheet trial of Carbarn's sheet reader: which stored lots go, what is compared, the marks and the summary."""
import copy
import json
from pathlib import Path

from fastapi.testclient import TestClient

from auction_bot.carbarn.models import parse_guide_tree
from auction_bot.carbarn.sheet_reader import SheetReader
from auction_bot.judge.requirements import sheet_needs
from auction_bot.review.app import create_app
from auction_bot.sheet_trial import load_trial, run_trial, save_marks, select_lots, summarize, trial_dir
from auction_bot.store import Store
from tests.test_pipeline_offline import cfg  # noqa: F401  (fixture)

FIX = Path(__file__).parent / "fixtures"
JPEG = b"\xff\xd8\xff" + bytes(20_000)


def _tree():
    return json.loads((FIX / "guide_223_tree.json").read_text(encoding="utf-8"))


def _rule(gid, code, notes, model="Hiace"):
    t = copy.deepcopy(_tree())
    t.update(id=gid, modelCode=code, notes=notes, model=model)
    return parse_guide_tree(t)


RULES = [_rule(1, "GDH206", "2, 3, 5 or 6 seats"), _rule(2, "ZVW30", "Cruise control", "Prius"),
         _rule(3, "NHP130", "Welcab with swivel or lift up seat", "Vitz"), _rule(4, "ZRR80", "No sticker paint", "Noah")]


def _reply(**sheet):
    r = json.loads((FIX / "carbarn_read_sheet_reply.json").read_text(encoding="utf-8"))
    r.pop("_note", None)
    r["sheet"].update(sheet)
    return r


def _seed(cfg, store, ref, gid, code, *, ocr=None, translator=None, soft=None, sheet=True, data=None, km=54_000, grade="4",
          chassis=None):
    d = cfg.evidence_dir / ref
    d.mkdir(parents=True, exist_ok=True)
    path = d / "sheet.jpg"
    if sheet:
        path.write_bytes(data or (JPEG + ref.encode()))
    facts = {"ref_no": ref, "url": f"https://autosearch.nichibojapan.com/auction/{ref}", "year": 2018, "make": "Toyota",
             "model": "Prius", "variant_text": "S", "chassis": chassis or code, "km": km, "grade_raw": grade, "grade": 4.0,
             "start_price_jpy": 500_000, "sheet_ocr": ocr, "translator": translator}
    return store.add_proposal(ref_no=ref, guide_id=gid, model_code=code, snapshot="s", rule_updated_at="u", status="NEEDS_REVIEW",
                              stage="judge", bid_jpy_k=1000, summary=f"2018 Toyota {code} {ref}", facts_json=facts,
                              hard_json={"checks": []}, soft_json=soft or {"mode": "judge", "requirements": []},
                              evidence_json={"dir": str(d), "sheet": str(path), "photos": []})


def test_sheet_needs():
    assert sheet_needs(RULES[0]) == {"seats", "sheet"}
    assert sheet_needs(RULES[1]) == {"cruise_control"}
    assert sheet_needs(RULES[2]) == {"welcab_seat"}
    assert sheet_needs(RULES[3]) == set()


def test_selection_order_and_exclusions(cfg):
    store = Store(cfg.db_path)
    seats = _seed(cfg, store, "A1", 1, "GDH206")
    cruise1 = _seed(cfg, store, "B1", 2, "ZVW30")
    cruise2 = _seed(cfg, store, "B2", 2, "ZVW30")
    welcab = _seed(cfg, store, "C1", 3, "NHP130")
    _seed(cfg, store, "D1", 4, "ZRR80")  # no sheet-type requirement: never sent
    _seed(cfg, store, "E1", 2, "ZVW30", sheet=False)  # no sheet on disk
    newer = _seed(cfg, store, "B1", 2, "ZVW30")  # the same lot again: only its newest row counts
    same_image = _seed(cfg, store, "F1", 2, "ZVW30", data=JPEG + b"B2")  # the same image as B2: sent once, newest row kept
    picked = select_lots(cfg, store, RULES, 10)
    ids = [p["pid"] for p in picked]
    assert ids[0] == welcab and set(ids[1:3]) == {newer, same_image} and ids[3] == seats and len(ids) == 4
    assert cruise1 not in ids and cruise2 not in ids
    assert [p["pid"] for p in select_lots(cfg, store, RULES, 2)] == ids[:2]
    assert [p["pid"] for p in select_lots(cfg, store, RULES, 10, ["GDH206"])] == [seats]
    store.close()


def test_run_compares_and_summarizes(cfg):
    store = Store(cfg.db_path)
    disagree = _seed(cfg, store, "B1", 2, "ZVW30", ocr={"available": True, "seats": 8, "model_code": "ZVW30", "km": 54_000,
                                                        "grade": "4", "is_4wd": False},
                     translator={"SalesPoint": "smart key", "Comments": "light scratches"},
                     soft={"mode": "judge", "requirements": [{"id": "r1", "text": "Cruise control", "source": "ladder", "kind": "must",
                                                              "topic": "cruise_control"}],
                           "judgement": {"requirements": [{"id": "r1", "verdict": "fail", "evidence": "photo 2: no stalk"}]}})
    agree = _seed(cfg, store, "B2", 2, "ZVW30", ocr={"available": True, "seats": 5, "model_code": "ZVW30", "km": 54_000,
                                                     "grade": "4", "is_4wd": True},
                  translator={"SalesPoint": "クルコン ナビ"})
    broken = _seed(cfg, store, "B3", 2, "ZVW30")
    replies = {"B1": (200, _reply()), "B2": (200, _reply()), "B3": (500, "server error")}

    def call(data, filename, mime):
        return replies[data[len(JPEG):].decode()]

    reader = SheetReader(cfg, call)
    path, summary = run_trial(cfg, store, reader, RULES, 10)
    trial = load_trial(cfg, path.stem.removeprefix("trial-"))
    lots = {lot["pid"]: lot for lot in trial["lots"]}
    one = lots[disagree]
    assert one["status"] == "ok" and one["evidence_base"] == "B1" and one["sheet_file"] == "sheet.jpg"
    rows = {r["field"]: r for r in one["rows"]}
    assert rows["seats"]["values"]["carbarn"] == 5 and rows["seats"]["values"]["ocr"] == 8 and rows["seats"]["disagree"]
    assert not rows["model code"]["disagree"] and not rows["odometer"]["disagree"] and not rows["grade"]["disagree"]
    assert rows["4WD"]["disagree"]  # the sheet line says 4WD, the OCR said no
    assert rows["cruise control"]["values"]["carbarn"].startswith("yes") and rows["cruise control"]["disagree"]
    assert "fail" in rows["cruise control"]["values"]["model"]
    assert rows["inspector notes"]["values"]["carbarn"] == "minor interior stains"
    assert not any(r["disagree"] for r in lots[agree]["rows"] if r["field"] in ("seats", "cruise control", "4WD"))
    assert lots[broken]["status"] == "error" and "500" in lots[broken]["error"]
    assert summary["lots"] == 3 and summary["ok"] == 2 and summary["failed"] == 1 and summary["calls"] == 3
    assert summary["decision"].startswith("keep today's reading")  # 1 of 3 failed: over the 10 % line before any mark
    assert "start_price_jpy" not in path.read_text(encoding="utf-8")
    # Jim's marks decide.
    save_marks(cfg, trial["stamp"], {str(disagree): {"seats": "carbarn", "4WD": "carbarn", "cruise control": "bot"}})
    s = summarize(load_trial(cfg, trial["stamp"]), cfg)
    assert s["marks"] == {"carbarn": 2, "bot": 1, "neither": 0}
    assert s["failure_rate"] > 0.10 and s["decision"].startswith("keep today's reading")  # 1 of 3 failed: over the 10 % line
    store.close()


def test_signed_out_stops_the_trial(cfg):
    from auction_bot.nichibo.browser import NotLoggedIn

    store = Store(cfg.db_path)
    for ref in ("B1", "B2", "B3"):
        _seed(cfg, store, ref, 2, "ZVW30")
    calls = []

    def signed_out(data, filename, mime):
        calls.append(1)
        raise NotLoggedIn("401")

    path, summary = run_trial(cfg, store, SheetReader(cfg, signed_out), RULES, 10)
    assert len(calls) == 1 and summary["signed_out"] and summary["lots"] == 1
    store.close()


def test_the_trial_page_and_marks(cfg):
    store = Store(cfg.db_path)
    pid = _seed(cfg, store, "B1", 2, "ZVW30", ocr={"available": True, "seats": 8})
    path, _ = run_trial(cfg, store, SheetReader(cfg, lambda d, f, m: (200, _reply())), RULES, 10)
    store.close()
    stamp = path.stem.removeprefix("trial-")
    client = TestClient(create_app(cfg))
    r = client.get("/sheet-trial")
    assert r.status_code == 200 and "Sheet reader trial" in r.text and "/evidence/B1/sheet.jpg" in r.text
    assert f'name="mark:{pid}:seats"' in r.text and "Carbarn right" in r.text and "marks needed" in r.text
    r = client.post(f"/sheet-trial/{stamp}/marks", data={f"mark:{pid}:seats": "carbarn", "mark:999:x": "bad"},
                    follow_redirects=False)
    assert r.status_code == 303
    marks = json.loads((trial_dir(cfg) / f"trial-{stamp}-marks.json").read_text(encoding="utf-8"))
    assert marks == {str(pid): {"seats": "carbarn"}}
    page = client.get("/sheet-trial").text
    assert "Carbarn right: 1" in page and "wire it in" in page
    assert client.get("/sheet-trial?stamp=../../etc").status_code == 404
    assert client.post("/sheet-trial/..%2Fx/marks", data={}).status_code == 404
