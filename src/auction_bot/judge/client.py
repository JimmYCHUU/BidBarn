"""OpenAI-compatible client for the free vision providers (Gemini first, Groq as fallback;
OpenAI stays opt-in, Anthropic is never used).

The `openai` SDK is used as a plain HTTP layer: chat completions with base64 image parts
and a JSON-schema response. Providers are tried in order; one that has no key, is over its
free daily quota, is rate limited, or fails hands over to the next. Daily call counts live
in the store so quotas survive restarts. Nothing here decides anything about a bid.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from auction_bot.nichibo.vehicle import sniff_image

DEFAULT_PROVIDERS: list[dict] = [
    {"name": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
     "model": "gemini-3.5-flash-lite", "api_key_env": "GEMINI_API_KEY", "rpm": 10, "rpd": 400, "vision": True},
    {"name": "gemini-3.1", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
     "model": "gemini-3.1-flash-lite", "api_key_env": "GEMINI_API_KEY", "rpm": 10, "rpd": 400, "vision": True},
    {"name": "groq", "base_url": "https://api.groq.com/openai/v1",
     "model": "openai/gpt-oss-120b", "api_key_env": "GROQ_API_KEY", "rpm": 20, "rpd": 800, "vision": False},
]
OPENAI_PROVIDER: dict = {"name": "openai", "base_url": None, "model": "gpt-5", "api_key_env": "OPENAI_API_KEY", "rpm": 30, "rpd": 5000}


class RateLimited(RuntimeError):
    """429 or an explicit quota message: try the next provider."""


class ServerError(RuntimeError):
    """5xx, timeout or connection trouble: try the next provider."""


class SchemaUnsupported(RuntimeError):
    """The provider rejected response_format json_schema: retry with json_object."""


@dataclass
class Provider:
    name: str
    base_url: str | None
    model: str
    api_key_env: str
    rpm: int = 10
    rpd: int = 400
    timeout: float = 90.0
    vision: bool = True  # False: text only, used only for calls that carry no image

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) or None

    @classmethod
    def from_dict(cls, d: dict) -> "Provider":
        return cls(name=str(d.get("name") or d.get("model") or "provider"), base_url=d.get("base_url") or None,
                   model=str(d["model"]), api_key_env=str(d.get("api_key_env") or "OPENAI_API_KEY"),
                   rpm=int(d.get("rpm") or 10), rpd=int(d.get("rpd") or 400), timeout=float(d.get("timeout") or 90),
                   vision=bool(d.get("vision", True)))


def providers_for(cfg) -> list[Provider]:
    """The provider list for the configured mode: `judge` -> config.yaml `judge.providers`
    (or the free defaults); `openai` -> the opt-in OpenAI entry with the configured model."""
    mode = (getattr(cfg, "sheet_check", "none") or "none").lower()
    if mode == "openai":
        return [Provider.from_dict(dict(OPENAI_PROVIDER, model=getattr(cfg, "model", "gpt-5")))]
    judge = getattr(cfg, "judge", None) or {}
    raw = judge.get("providers") or DEFAULT_PROVIDERS
    return [Provider.from_dict(d) for d in raw]


@dataclass
class CallResult:
    parsed: dict | None
    provider: str | None = None
    model: str | None = None
    usage: dict | None = None
    unavailable: str | None = None  # why no provider answered
    attempts: list[str] = field(default_factory=list)
    raw: str | None = None

    @property
    def ok(self) -> bool:
        return self.parsed is not None


def image_part(path: Path, detail: str = "high") -> dict | None:
    """A base64 `image_url` content part for chat completions, or None for a non-image file."""
    data = Path(path).read_bytes()
    kind = sniff_image(data)
    if kind is None:
        return None
    b64 = base64.standard_b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{kind[0]};base64,{b64}", "detail": detail}}


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def extract_json(text: str) -> dict | None:
    """The first JSON object in a model reply, tolerating code fences and chatter around it."""
    if not text:
        return None
    body = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
    if m:
        body = m.group(1)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def openai_transport(p: Provider, messages: list[dict], response_format: dict, max_tokens: int) -> tuple[str, dict | None]:
    """One chat-completions call through the openai SDK against `p.base_url`."""
    import openai

    client = openai.OpenAI(base_url=p.base_url, api_key=p.api_key, timeout=p.timeout, max_retries=0)
    extra = {"response_format": response_format} if response_format else {}
    try:
        r = client.chat.completions.create(model=p.model, messages=messages, max_tokens=max_tokens, temperature=0, **extra)
    except openai.RateLimitError as exc:
        raise RateLimited(str(exc)[:200]) from exc
    except (openai.APITimeoutError, openai.APIConnectionError) as exc:
        raise ServerError(str(exc)[:200]) from exc
    except openai.APIStatusError as exc:
        msg = str(exc)
        if exc.status_code >= 500:
            raise ServerError(msg[:200]) from exc
        if exc.status_code == 400 and response_format \
                and re.search(r"response_format|json_schema|schema|validate JSON|failed_generation|json", msg, re.I):
            raise SchemaUnsupported(msg[:200]) from exc
        if exc.status_code == 429 or re.search(r"quota|rate limit", msg, re.I):
            raise RateLimited(msg[:200]) from exc
        raise
    choice = r.choices[0] if r.choices else None
    text = (choice.message.content if choice and choice.message else "") or ""
    usage = None
    if getattr(r, "usage", None):
        usage = {"input": getattr(r.usage, "prompt_tokens", None), "output": getattr(r.usage, "completion_tokens", None)}
    return text, usage


class MemoryLedger:
    """Daily call counts kept in memory (tests); the store-backed one is `StoreLedger`."""

    def __init__(self):
        self.calls: dict[str, int] = {}
        self.tokens: dict[str, int] = {}

    def calls_today(self, provider: str) -> int:
        return self.calls.get(provider, 0)

    def record(self, provider: str, tokens: int = 0) -> None:
        self.calls[provider] = self.calls.get(provider, 0) + 1
        self.tokens[provider] = self.tokens.get(provider, 0) + tokens


class StoreLedger:
    def __init__(self, store):
        self.store = store

    def calls_today(self, provider: str) -> int:
        return self.store.model_calls_today(provider)

    def record(self, provider: str, tokens: int = 0) -> None:
        self.store.record_model_call(provider, tokens)


def make_client(cfg, store, transport: Callable | None = None) -> "JudgeClient":
    """The client the pipeline uses: the configured providers with daily counts kept in the store."""
    return JudgeClient(providers_for(cfg), ledger=StoreLedger(store), transport=transport)


class JudgeClient:
    def __init__(self, providers: list[Provider], ledger=None, transport: Callable | None = None, sleep: Callable = time.sleep,
                 clock: Callable = time.time):
        self.providers = providers
        self.ledger = ledger or MemoryLedger()
        self._transport = transport or openai_transport
        self._sleep = sleep
        self._clock = clock
        self._last_call: dict[str, float] = {}

    def available(self) -> list[Provider]:
        return [p for p in self.providers if p.api_key and self.ledger.calls_today(p.name) < p.rpd]

    def _pace(self, p: Provider) -> None:
        if p.rpm <= 0:
            return
        gap = 60.0 / p.rpm
        wait = gap - (self._clock() - self._last_call.get(p.name, -gap))
        if wait > 0:
            self._sleep(wait)
        self._last_call[p.name] = self._clock()

    def complete(self, messages: list[dict], schema, max_tokens: int = 4000) -> CallResult:
        """Ask the first provider that can answer; validate the reply against `schema` (pydantic)."""
        attempts: list[str] = []
        json_schema = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}}
        has_images = any(isinstance(m.get("content"), list) and any(part.get("type") == "image_url" for part in m["content"])
                         for m in messages)
        for p in self.providers:
            if not p.api_key:
                attempts.append(f"{p.name}: no {p.api_key_env} in .env")
                continue
            if has_images and not p.vision:
                attempts.append(f"{p.name}: text only, cannot look at images")
                continue
            if self.ledger.calls_today(p.name) >= p.rpd:
                attempts.append(f"{p.name}: daily quota of {p.rpd} calls used")
                continue
            self._pace(p)
            text: str | None = None
            usage = None
            try:
                try:
                    text, usage = self._transport(p, messages, json_schema, max_tokens)
                except SchemaUnsupported:
                    # Strict mode refused: ask for a JSON object, then as a last resort plain text we parse ourselves.
                    fallback = list(messages) + [{"role": "user", "content": "Reply with one JSON object only, following this schema:\n"
                                                  + json.dumps(schema.model_json_schema())}]
                    try:
                        text, usage = self._transport(p, fallback, {"type": "json_object"}, max_tokens)
                    except SchemaUnsupported:
                        text, usage = self._transport(p, fallback, {}, max_tokens)
            except RateLimited as exc:
                attempts.append(f"{p.name}: rate limited ({exc})")
                continue
            except ServerError as exc:
                attempts.append(f"{p.name}: unavailable ({exc})")
                continue
            except Exception as exc:  # bad key, bad model name: never break the scan
                attempts.append(f"{p.name}: {str(exc)[:160]}")
                continue
            tokens = int((usage or {}).get("input") or 0) + int((usage or {}).get("output") or 0)
            self.ledger.record(p.name, tokens)
            data = extract_json(text or "")
            if data is None:
                attempts.append(f"{p.name}: reply was not JSON")
                continue
            try:
                parsed = schema.model_validate(data).model_dump()
            except Exception as exc:
                attempts.append(f"{p.name}: reply did not fit the schema ({str(exc)[:120]})")
                continue
            return CallResult(parsed=parsed, provider=p.name, model=p.model, usage=usage, attempts=attempts, raw=text)
        return CallResult(parsed=None, unavailable="; ".join(attempts) or "no providers configured", attempts=attempts)
