"""The judgement client: provider order, fallback, quotas, JSON handling. No network anywhere."""
from pathlib import Path

import pytest
from pydantic import BaseModel

from auction_bot.config import Config
from auction_bot.judge.client import (DEFAULT_PROVIDERS, JudgeClient, MemoryLedger, Provider, RateLimited, SchemaUnsupported,
                                      ServerError, StoreLedger, extract_json, image_part, providers_for, text_part)
from auction_bot.store import Store


class Answer(BaseModel):
    verdict: str
    n: int = 0


def _providers(monkeypatch, gemini=True, groq=True):
    if gemini:
        monkeypatch.setenv("GEMINI_API_KEY", "g")
    else:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    if groq:
        monkeypatch.setenv("GROQ_API_KEY", "q")
    else:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
    return [Provider.from_dict(DEFAULT_PROVIDERS[0]), Provider.from_dict(dict(DEFAULT_PROVIDERS[-1], vision=True))]  # gemini, groq


def test_providers_for_config(monkeypatch):
    cfg = Config()
    cfg.sheet_check = "judge"
    assert [p.name for p in providers_for(cfg)] == ["gemini", "gemini-3.1", "groq"]
    assert [p.vision for p in providers_for(cfg)] == [True, True, False]
    cfg.judge = {"providers": [{"name": "mine", "base_url": "http://x", "model": "m", "api_key_env": "MY_KEY", "rpd": 5}]}
    p = providers_for(cfg)[0]
    assert (p.name, p.base_url, p.model, p.api_key_env, p.rpd) == ("mine", "http://x", "m", "MY_KEY", 5)
    cfg.sheet_check = "openai"
    cfg.model = "gpt-5-mini"
    p = providers_for(cfg)[0]
    assert p.name == "openai" and p.base_url is None and p.model == "gpt-5-mini" and p.api_key_env == "OPENAI_API_KEY"


def test_extract_json_and_parts(tmp_path):
    assert extract_json('Sure! ```json\n{"verdict": "pass", "n": 2}\n```') == {"verdict": "pass", "n": 2}
    assert extract_json('prefix {"a": {"b": 1}} suffix') == {"a": {"b": 1}}
    assert extract_json("no json here") is None and extract_json("[1, 2]") is None
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\xff\xd8\xff" + bytes(100))
    part = image_part(img)
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/jpeg;base64,") and part["image_url"]["detail"] == "high"
    (tmp_path / "x.txt").write_bytes(b"hello")
    assert image_part(tmp_path / "x.txt") is None
    assert text_part("hi") == {"type": "text", "text": "hi"}


def test_first_provider_answers(monkeypatch):
    calls = []

    def transport(p, messages, response_format, max_tokens):
        calls.append((p.name, response_format["type"]))
        return '{"verdict": "pass", "n": 1}', {"input": 100, "output": 20}

    c = JudgeClient(_providers(monkeypatch), transport=transport, sleep=lambda s: None)
    r = c.complete([{"role": "user", "content": "x"}], Answer)
    assert r.ok and r.parsed == {"verdict": "pass", "n": 1} and r.provider == "gemini" and r.usage == {"input": 100, "output": 20}
    assert calls == [("gemini", "json_schema")] and c.ledger.calls_today("gemini") == 1 and c.ledger.tokens["gemini"] == 120


def test_schema_unsupported_falls_back_to_json_object(monkeypatch):
    seen = []

    def transport(p, messages, response_format, max_tokens):
        seen.append(response_format["type"])
        if response_format["type"] == "json_schema":
            raise SchemaUnsupported("response_format not supported")
        assert "schema" in messages[-1]["content"].lower()
        return '{"verdict": "fail"}', None

    r = JudgeClient(_providers(monkeypatch), transport=transport, sleep=lambda s: None).complete([{"role": "user", "content": "x"}], Answer)
    assert r.ok and r.parsed["verdict"] == "fail" and seen == ["json_schema", "json_object"]


def test_rate_limit_and_server_errors_move_to_the_next_provider(monkeypatch):
    def transport(p, messages, response_format, max_tokens):
        if p.name == "gemini":
            raise RateLimited("429")
        return '{"verdict": "pass"}', None

    r = JudgeClient(_providers(monkeypatch), transport=transport, sleep=lambda s: None).complete([], Answer)
    assert r.ok and r.provider == "groq" and r.attempts == ["gemini: rate limited (429)"]

    def broken(p, messages, response_format, max_tokens):
        raise ServerError("502") if p.name == "gemini" else RuntimeError("bad key")

    r = JudgeClient(_providers(monkeypatch), transport=broken, sleep=lambda s: None).complete([], Answer)
    assert not r.ok and "gemini: unavailable (502)" in r.unavailable and "groq: bad key" in r.unavailable


def test_missing_keys_and_daily_quota(monkeypatch):
    providers = _providers(monkeypatch, gemini=False, groq=True)
    hits = []
    ledger = MemoryLedger()
    ledger.calls["groq"] = 800
    c = JudgeClient(providers, ledger=ledger, transport=lambda *a: hits.append(a) or ('{"verdict":"x"}', None), sleep=lambda s: None)
    r = c.complete([], Answer)
    assert not r.ok and hits == [] and r.unavailable == "gemini: no GEMINI_API_KEY in .env; groq: daily quota of 800 calls used"
    assert c.available() == []
    ledger.calls["groq"] = 799
    assert [p.name for p in c.available()] == ["groq"] and c.complete([], Answer).provider == "groq"


def test_bad_replies_move_on(monkeypatch):
    def transport(p, messages, response_format, max_tokens):
        return ("not json at all", None) if p.name == "gemini" else ('{"n": "not a number"}', None)

    r = JudgeClient(_providers(monkeypatch), transport=transport, sleep=lambda s: None).complete([], Answer)
    assert not r.ok and "gemini: reply was not JSON" in r.unavailable and "groq: reply did not fit the schema" in r.unavailable


def test_rpm_pacing_uses_the_clock(monkeypatch):
    now = {"t": 1000.0}
    slept = []
    providers = [Provider("p", "http://x", "m", "GEMINI_API_KEY", rpm=30, rpd=100)]
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    c = JudgeClient(providers, transport=lambda *a: ('{"verdict":"ok"}', None), sleep=slept.append, clock=lambda: now["t"])
    c.complete([], Answer)
    now["t"] += 0.5
    c.complete([], Answer)
    assert slept == [pytest.approx(1.5)]  # 30 per minute = one every 2 s; the second call came 0.5 s later


def test_store_ledger_persists_per_day(tmp_path):
    s = Store(tmp_path / "x.sqlite")
    ledger = StoreLedger(s)
    assert ledger.calls_today("gemini") == 0
    ledger.record("gemini", 120)
    ledger.record("gemini", 80)
    assert ledger.calls_today("gemini") == 2 and s.model_calls_today("groq") == 0
    assert s.model_calls_today("gemini", day="1999-01-01") == 0
    row = s.conn.execute("SELECT tokens FROM model_calls WHERE provider='gemini'").fetchone()
    assert row["tokens"] == 200
    s.close()


def test_text_only_providers_are_skipped_when_images_are_attached(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "q")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    providers = [Provider("groq", "http://groq", "gpt-oss", "GROQ_API_KEY", vision=False),
                 Provider("gemini", "http://gemini", "flash-lite", "GEMINI_API_KEY", vision=True)]
    seen = []
    c = JudgeClient(providers, transport=lambda p, *a: seen.append(p.name) or ('{"verdict": "ok"}', None), sleep=lambda s: None)
    with_image = [{"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="}}]}]
    r = c.complete(with_image, Answer)
    assert r.provider == "gemini" and seen == ["gemini"] and r.attempts == ["groq: text only, cannot look at images"]
    r = c.complete([{"role": "user", "content": "text only"}], Answer)
    assert r.provider == "groq"
    assert Provider.from_dict({"model": "m", "vision": False}).vision is False and Provider.from_dict({"model": "m"}).vision is True
