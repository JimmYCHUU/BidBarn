"""Read Team Bid bidding guides from the Carbarn dashboard backend.

The dashboard authenticates its API calls itself (cookie or bearer token; the
HAR capture was sanitised so we do not assume which). We therefore load the
bidding view in the shared browser profile, capture the headers the dashboard
sends on its own bidding-guides request, and replay them for the tree calls.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from auction_bot.carbarn.models import ModelRule, parse_guide_tree
from auction_bot.config import Config
from auction_bot.nichibo.browser import CARBARN_READ_SHEET_PATH, Browser, NotLoggedIn

_KEEP_HEADERS = {"authorization", "cookie", "x-auth-token", "x-xsrf-token", "accept", "origin", "referer", "user-agent"}


class CarbarnClient:
    def __init__(self, browser: Browser, cfg: Config):
        self.browser = browser
        self.cfg = cfg
        self.api_base = cfg.carbarn.get("api_base", "https://www.cbs.s1.carbarn.com.au").rstrip("/")
        self.dashboard_url = cfg.carbarn.get("dashboard_url", "https://dashboard.carbarn.com.au/?view=bidding")
        self._headers: dict[str, str] | None = None
        self._list_cache: list[dict] | None = None

    # -- auth --------------------------------------------------------------
    def _capture(self) -> None:
        page = self.browser.page
        try:
            with page.expect_response(
                lambda r: "/core/api/admin/bidding-guides?" in r.url and r.status == 200, timeout=90_000
            ) as info:
                self.browser.goto(self.dashboard_url)
            resp = info.value
        except Exception as exc:  # timeout: most likely the login page
            raise NotLoggedIn(
                "Carbarn dashboard did not load the bidding guides. Run `bot login` and sign in to "
                f"{self.dashboard_url} in the bot's browser window. ({exc.__class__.__name__})"
            ) from exc
        self._headers = {k: v for k, v in resp.request.headers.items() if k.lower() in _KEEP_HEADERS}
        body = resp.json()
        self._list_cache = body.get("content", body if isinstance(body, list) else [])

    def _api_get(self, path: str) -> dict | list:
        if self._headers is None:
            self._capture()
        r = self.browser.get(self.api_base + path, headers=self._headers)
        if r.status in (401, 403):
            raise NotLoggedIn(f"Carbarn API returned {r.status} for {path}; run `bot login`.")
        if not r.ok:
            raise RuntimeError(f"Carbarn API {path} -> {r.status}: {r.text()[:200]}")
        return r.json()

    def read_sheet(self, data: bytes, filename: str, mime: str, timeout_s: float = 300.0) -> tuple[int, object]:
        """Carbarn's own auction-sheet reader (Jim, 2026-10-22): (HTTP status, JSON body or text). The dashboard
        runs its paid model on the image; the bot sends the sheet image and nothing else."""
        if self._headers is None:
            self._capture()
        r = self.browser.post_multipart(self.api_base + CARBARN_READ_SHEET_PATH, "file", filename, data, mime,
                                        headers=self._headers, timeout_s=timeout_s)
        if r.status in (401, 403):
            raise NotLoggedIn(f"Carbarn API returned {r.status} for the sheet reader; run `bot login`.")
        try:
            return r.status, r.json()
        except Exception:  # an error page or an empty body
            return r.status, (r.text() or "")[:300]

    # -- data --------------------------------------------------------------
    def list_guides(self) -> list[dict]:
        if self._list_cache is None:
            data = self._api_get("/core/api/admin/bidding-guides?page=0&size=2000")
            self._list_cache = data.get("content", []) if isinstance(data, dict) else data
        return self._list_cache

    def guide_tree(self, guide_id: int) -> dict:
        return self._api_get(f"/core/api/admin/bidding-guides/{guide_id}/tree")

    def fetch_team_guides(self, model_codes: list[str] | None = None, include_inactive: bool = False) -> list[dict]:
        """Return [{"item": <list item>, "tree": <tree>}] for Team Bid guides."""
        want_boss = bool(self.cfg.carbarn.get("boss_bid", False))
        wanted = {c.upper() for c in model_codes} if model_codes else None
        out = []
        for item in self.list_guides():
            if bool(item.get("bossBid", False)) != want_boss:
                continue
            if not include_inactive and not item.get("active", True):
                continue
            if wanted and str(item.get("modelCode", "")).upper() not in wanted:
                continue
            out.append({"item": item, "tree": self.guide_tree(int(item["id"]))})
        return out


# -- snapshots ---------------------------------------------------------------

def save_snapshot(cfg: Config, guides: list[dict]) -> Path:
    fetched_at = datetime.now(timezone.utc)
    path = cfg.snapshot_dir / f"rules_{fetched_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps({"fetched_at": fetched_at.isoformat(), "guides": guides}, ensure_ascii=False), encoding="utf-8")
    return path


def latest_snapshot(cfg: Config) -> Path | None:
    files = sorted(cfg.snapshot_dir.glob("rules_*.json"))
    return files[-1] if files else None


def snapshot_age_hours(path: Path) -> float:
    data = json.loads(path.read_text(encoding="utf-8"))
    fetched = datetime.fromisoformat(data["fetched_at"])
    return (datetime.now(timezone.utc) - fetched).total_seconds() / 3600.0


def load_override(cfg: Config, model_code: str) -> str:
    p = cfg.override_dir / f"{model_code.upper()}.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def append_override(cfg: Config, model_code: str, text: str, author: str = "review") -> Path:
    p = cfg.override_dir / f"{model_code.upper()}.md"
    stamp = datetime.now().strftime("%Y-%m-%d")
    line = f"- ({stamp}, {author}) {text.strip()}\n"
    if not p.exists():
        p.write_text(f"# Standing instructions for {model_code.upper()}\n\n", encoding="utf-8")
    with p.open("a", encoding="utf-8") as f:
        f.write(line)
    return p


def load_rules(cfg: Config, path: Path | None = None, model_codes: list[str] | None = None) -> tuple[list[ModelRule], Path]:
    path = path or latest_snapshot(cfg)
    if path is None:
        raise FileNotFoundError("No rules snapshot found. Run `bot fetch-rules` first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    wanted = {c.upper() for c in model_codes} if model_codes else None
    rules: list[ModelRule] = []
    for g in data["guides"]:
        rule = parse_guide_tree(g["tree"], g.get("item"))
        if wanted and rule.model_code.upper() not in wanted:
            continue
        rule.override_text = load_override(cfg, rule.model_code)
        rules.append(rule)
    return rules, path
