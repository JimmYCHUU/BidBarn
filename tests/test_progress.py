"""The PROGRESS / SUMMARY lines a command prints for the dashboard, and the cancel file."""
import json

import pytest

from auction_bot import progress


def test_silent_without_the_variable(monkeypatch, capsys):
    monkeypatch.delenv("BIDBARN_PROGRESS", raising=False)
    assert not progress.enabled()
    progress.emit(stage="scan", guide=1)
    progress.summary({"guides": 1})
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""  # a console run prints exactly what it printed before


def test_one_parseable_line_each(monkeypatch, capsys):
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    assert progress.enabled()
    progress.emit(stage="scan", guides=46, guide=3, code="GDH206", errors=0)
    progress.summary({"guides": 46, "errors": ["GDH206: search failed"]})
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and lines[0].startswith("PROGRESS {") and lines[1].startswith("SUMMARY {")
    assert progress.parse(lines[0]) == ("progress", {"stage": "scan", "guides": 46, "guide": 3, "code": "GDH206", "errors": 0})
    assert progress.parse(lines[1]) == ("summary", {"guides": 46, "errors": ["GDH206: search failed"]})
    assert ", " not in lines[0] and ": " not in lines[0].split(" ", 1)[1]  # compact


def test_parse_good_bad_and_ordinary_lines():
    assert progress.parse('PROGRESS {"i":1}\r\n') == ("progress", {"i": 1})
    assert progress.parse('SUMMARY {"ok":true}') == ("summary", {"ok": True})
    assert progress.parse("PROGRESS {not json") is None
    assert progress.parse("PROGRESS [1, 2]") is None  # an object, nothing else
    assert progress.parse("SUMMARY") is None and progress.parse("PROGRESS ") is None
    assert progress.parse("12:00:01 INFO == GDH206 Toyota Hiace") is None
    assert progress.parse("  PROGRESS {}") is None  # only at the start of a line
    assert progress.parse("STOPPED: stopped from the dashboard") is None
    assert progress.parse("") is None and progress.parse(None) is None


def test_values_outside_ascii_survive_any_console(monkeypatch, capsys):
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    message = "乗車定員 → 8 ✓"
    progress.emit(stage="place", message=message, when=object)  # `object` is not JSON: written as text, no error
    line = capsys.readouterr().out.strip()
    line.encode("ascii")  # nothing a cp1252 console could choke on
    assert "\n" not in line and progress.parse(line)[1]["message"] == message
    progress.summary({"note": "line one\nline two"})
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and json.loads(out[len("SUMMARY "):])["note"] == "line one\nline two"


def test_emit_never_raises(monkeypatch, capsys):
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")

    class Broken:
        def write(self, text):
            raise OSError("the console is gone")

        def flush(self):
            pass

    monkeypatch.setattr(progress.sys, "stdout", Broken())
    progress.emit(stage="scan")
    progress.summary({"a": 1})


def test_check_cancel(monkeypatch, tmp_path):
    monkeypatch.delenv("BIDBARN_CANCEL_FILE", raising=False)
    assert not progress.cancel_requested()
    progress.check_cancel()  # no variable: never cancelled
    flag = tmp_path / "7.cancel"
    monkeypatch.setenv("BIDBARN_CANCEL_FILE", str(flag))
    assert not progress.cancel_requested()
    progress.check_cancel()  # the file is not there yet
    flag.write_text("stop", encoding="utf-8")
    assert progress.cancel_requested()
    with pytest.raises(progress.Cancelled, match="stopped from the dashboard"):
        progress.check_cancel()
    assert issubclass(progress.Cancelled, RuntimeError)  # so the CLI prints it as one STOPPED line
