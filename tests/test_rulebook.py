"""The rulebook's catalogue and the answers kept as versions."""
from types import SimpleNamespace

import pytest

from auction_bot import rulebook
from auction_bot.store import Store

NINE = ["odo_margin", "interior_years", "selected_pair", "no_selected_pair", "model_code_unmatched",
        "welcab_chips", "welcab_refinements", "guide_213271", "gdh206k"]


def test_the_catalogue_has_every_open_question_once():
    ids = [q.id for q in rulebook.OPEN]
    assert ids == NINE
    assert len(set(ids)) == len(ids)
    assert [q.id for q in rulebook.OPEN if q.kind == "setting"] == ["odo_margin"]
    assert all(q.today for q in rulebook.OPEN if q.kind == "confirm")
    margin = rulebook.question("odo_margin")
    assert margin.setting == "odo_margin_km" and margin.today == 0
    assert [c.value for c in margin.choices] == [0, 1000, 2000, 3000, 5000]
    assert len({d.id for d in rulebook.DECIDED}) == len(rulebook.DECIDED) > 10


def test_values_are_limited_to_the_catalogue():
    margin = rulebook.question("odo_margin")
    assert rulebook.valid_value(margin, 2000)
    assert not rulebook.valid_value(margin, 1500)
    assert not rulebook.valid_value(margin, "2000")
    assert not rulebook.valid_value(margin, True)
    gdh = rulebook.question("gdh206k")
    assert rulebook.valid_value(gdh, "keep")
    assert not rulebook.valid_value(gdh, "change")
    assert not rulebook.valid_value(gdh, 0)


def test_answers_are_versions_that_never_change(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    assert s.rulebook_version() == 0 and s.rulebook_answers() == {} and s.rulebook_history() == []
    assert s.add_rulebook_answer("odo_margin", 2000, note="try it", preview_run=7) == 1
    assert s.rulebook_answers() == {"odo_margin_km": 2000}
    assert s.add_rulebook_answer("gdh206k", "keep") == 2
    assert s.rulebook_version() == 2
    assert s.rulebook_answers() == {"odo_margin_km": 2000, "gdh206k": "keep"}
    hist = s.rulebook_history()
    assert [r["version"] for r in hist] == [2, 1]
    assert hist[1]["question_id"] == "odo_margin" and hist[1]["note"] == "try it" and hist[1]["preview_run"] == 7
    assert '"odo_margin_km"' in hist[1]["answers_json"] and "gdh206k" not in hist[1]["answers_json"]
    s.add_rulebook_answer("odo_margin", 0)
    assert [r["answers_json"] for r in s.rulebook_history()][-1] == hist[1]["answers_json"]
    assert s.rulebook_answers()["odo_margin_km"] == 0
    s.close()


def test_change_requests_are_kept_and_empty_ones_refused(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    with pytest.raises(ValueError):
        s.add_rule_request("gdh206k", "   ")
    first = s.add_rule_request("gdh206k", "Treat it as skip")
    second = s.add_rule_request("odo_margin", "Per model please")
    assert second > first
    rows = s.rule_requests()
    assert [r["text"] for r in rows] == ["Treat it as skip", "Per model please"]
    assert rows[0]["question_id"] == "gdh206k" and rows[0]["created_at"]
    s.close()


def test_current_answers_without_a_database_is_empty(tmp_path):
    db = tmp_path / "none.sqlite"
    assert rulebook.current_answers(SimpleNamespace(db_path=db)) == {}
    assert not db.exists()
    s = Store(db)
    s.add_rulebook_answer("odo_margin", 1000)
    s.close()
    assert rulebook.current_answers(SimpleNamespace(db_path=db)) == {"odo_margin_km": 1000}


def test_nothing_locked_can_be_answered():
    ids = {q.id for q in rulebook.OPEN}
    titles = {q.title for q in rulebook.OPEN}
    for item in rulebook.LOCKED:
        assert item.text not in ids and item.text not in titles and item.source
    assert len(rulebook.LOCKED) >= 7
    with pytest.raises(KeyError):
        rulebook.question("testing_lock")
