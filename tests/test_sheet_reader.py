"""Carbarn's sheet reader: the one POST the bot may send to the dashboard's API, the cache, and failures (no network)."""
import json
from pathlib import Path

import pytest

from auction_bot.carbarn.sheet_reader import SheetReader
from auction_bot.config import Config
from auction_bot.nichibo.browser import Browser, ForbiddenUrl, NotLoggedIn, carbarn_post_allowed
from auction_bot.progress import Cancelled

FIX = Path(__file__).parent / "fixtures"
READ = "https://www.cbs.s1.carbarn.com.au/auc/api/admin/aleado-auction/sheet-discovery/read-sheet"
JPEG = b"\xff\xd8\xff" + bytes(20_000)


def _reply():
    return json.loads((FIX / "carbarn_read_sheet_reply.json").read_text(encoding="utf-8"))


def _cfg(tmp_path) -> Config:
    cfg = Config()
    cfg.db_path = tmp_path / "bot.sqlite"
    return cfg


def _sheet(tmp_path, name="sheet.jpg", data=JPEG) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_only_the_sheet_reader_may_receive_a_post():
    assert carbarn_post_allowed(READ)[0]
    for url in [READ + "?x=1", READ + "#f", READ.replace("https:", "http:"), READ.replace("read-sheet", "other-endpoint"),
                "https://www.cbs.s1.carbarn.com.au/auc/api/admin/aleado-auction/sheet-discovery/example/9/write",
                "https://www.cbs.s1.carbarn.com.au/auc/api/admin/aleado-auction/sheet-discovery/read-sheet-v2",
                "https://www.cbs.s1.carbarn.com.au/auc/api/admin/aleado-auction/sheet-discovery/read-sheet/extra",
                "https://www.cbs.s1.carbarn.com.au/core/api/admin/bidding-guides",
                "https://www.cbs.s1.carbarn.com.au.evil.example/auc/api/admin/aleado-auction/sheet-discovery/read-sheet",
                "https://user@www.cbs.s1.carbarn.com.au/auc/api/admin/aleado-auction/sheet-discovery/read-sheet",
                "https://www.cbs.s1.carbarn.com.au:8443/auc/api/admin/aleado-auction/sheet-discovery/read-sheet",
                "https://autosearch.nichibojapan.com/auction/bid"]:
        assert not carbarn_post_allowed(url)[0], url


def test_browser_post_refuses_before_any_request():
    b = Browser(Config())
    b.context = object()  # never touched: the guard runs first
    with pytest.raises(ForbiddenUrl):
        b.post_multipart("https://www.cbs.s1.carbarn.com.au/core/api/admin/bidding-guides", "file", "x.jpg", JPEG, "image/jpeg")


def test_read_caches_by_image_and_drops_prices(tmp_path):
    calls = []

    def call(data, filename, mime):
        calls.append((len(data), filename, mime))
        return 200, _reply()

    reader = SheetReader(_cfg(tmp_path), call)
    first = reader.read(_sheet(tmp_path))
    assert first.ok and not first.cached and first.error is None and len(calls) == 1
    assert calls[0][2] == "image/jpeg" and calls[0][1].endswith(".jpg")
    assert "start_price_jpy" not in first.reply["sheet"] and "recycling_deposit_jpy" not in first.reply["sheet"]
    assert first.reply["sheet"]["model_code"] == "DAA-ZVW30"
    # The same image under another name is the same sheet: no second call.
    again = reader.read(_sheet(tmp_path, "copy.jpg"))
    assert again.ok and again.cached and again.reply == first.reply and len(calls) == 1 and reader.calls == 1
    stored = list((tmp_path / "cache" / "sheet_reads").glob("*.json"))
    assert len(stored) == 1 and stored[0].stem == first.sha1
    assert "start_price_jpy" not in stored[0].read_text(encoding="utf-8")


def test_failures_are_reported_and_never_cached(tmp_path):
    answers = iter([(500, "server error"), (200, "not json"), (200, {**_reply(), "sheetError": "model busy"}), (200, _reply())])
    reader = SheetReader(_cfg(tmp_path), lambda d, f, m: next(answers))
    sheet = _sheet(tmp_path)
    r1 = reader.read(sheet)
    assert not r1.ok and "500" in r1.error
    r2 = reader.read(sheet)
    assert not r2.ok and "reply" in r2.error
    r3 = reader.read(sheet)  # a partial answer is returned but not kept: the next read asks again
    assert r3.ok and r3.reply["sheetError"] == "model busy" and not r3.cached
    r4 = reader.read(sheet)
    assert r4.ok and not r4.cached and reader.calls == 4
    assert reader.read(sheet).cached


def test_timeouts_and_errors_do_not_raise(tmp_path):
    def boom(d, f, m):
        raise TimeoutError("Timeout 300000ms exceeded")

    r = SheetReader(_cfg(tmp_path), boom).read(_sheet(tmp_path))
    assert not r.ok and "Timeout" in r.error


def test_signed_out_stops_every_later_call(tmp_path):
    calls = []

    def signed_out(d, f, m):
        calls.append(1)
        raise NotLoggedIn("Carbarn API returned 401; run `bot login`.")

    reader = SheetReader(_cfg(tmp_path), signed_out)
    assert not reader.read(_sheet(tmp_path)).ok and reader.signed_out
    r = reader.read(_sheet(tmp_path, "b.jpg", JPEG + b"x"))
    assert not r.ok and "signed out" in r.error.lower() and len(calls) == 1


def test_cancel_is_never_swallowed_and_non_images_are_refused(tmp_path):
    def cancel(d, f, m):
        raise Cancelled("stopped from the dashboard")

    with pytest.raises(Cancelled):
        SheetReader(_cfg(tmp_path), cancel).read(_sheet(tmp_path))
    calls = []
    r = SheetReader(_cfg(tmp_path), lambda d, f, m: calls.append(1)).read(_sheet(tmp_path, "x.jpg", b"<html>"))
    assert not r.ok and "not an image" in r.error and calls == []
    r = SheetReader(_cfg(tmp_path), lambda d, f, m: calls.append(1)).read(tmp_path / "missing.jpg")
    assert not r.ok and calls == []


def test_a_per_run_cap(tmp_path):
    reader = SheetReader(_cfg(tmp_path), lambda d, f, m: (200, _reply()), max_calls=1)
    assert reader.read(_sheet(tmp_path, "a.jpg", JPEG + b"a")).ok
    capped = reader.read(_sheet(tmp_path, "b.jpg", JPEG + b"b"))
    assert not capped.ok and "cap" in capped.error and reader.calls == 1
    assert reader.read(_sheet(tmp_path, "a2.jpg", JPEG + b"a")).cached  # the cache still answers
