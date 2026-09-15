"""Watch mode (`bot watch`): record how Jim works on Nichibo, without doing anything itself.

The bot's signed-in browser is opened and Jim drives it by hand. Everything he does is
written to `data/sessions/<stamp>/events.jsonl` with a timestamp: page navigations, clicks
(with the element clicked), scrolling, hovers over the photo gallery, the sheet and the
search cards, what he types into the bid form, the bid calls his clicks cause, and the
reasons he types into a small box in the corner of the window. A screenshot is taken at
every navigation and every bid.

In this mode the bot makes no request of its own and never touches the bid form; every bid
is Jim's own manual bid. The Stock URL guard is off for this window (it would block his own
clicks), which is the one documented exception in CLAUDE.md. `bot watch --report DIR`
turns a recorded session into a per-lot timeline, offline.
"""
from __future__ import annotations

import json
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from auction_bot.config import Config

NOT_A_LOT = {"search", "cart", "bid", "login", "jsonAuctionSheetData", "jsonSuggested", "jsonMaketPrice"}

# Injected into every Nichibo page. Sends events through window.__bidbarn_event and mounts
# the reason box. Nothing here clicks, submits or changes anything on the page.
INIT_SCRIPT = r"""
(() => {
  if (window.__bidbarnWatching) return;
  if (!/(^|\.)nichibojapan\.com$/i.test(location.hostname)) return;
  window.__bidbarnWatching = true;
  const send = (type, data) => {
    try { window.__bidbarn_event(JSON.stringify(Object.assign({type: type, url: location.href, t_page: Date.now()}, data || {}))); } catch (e) {}
  };
  const describe = (el) => {
    if (!el || !el.tagName) return {};
    const d = {tag: el.tagName.toLowerCase()};
    if (el.id) d.id = el.id;
    if (el.className && typeof el.className === 'string') d.cls = el.className.trim().slice(0, 80);
    const text = (el.innerText || el.value || el.alt || '').trim().replace(/\s+/g, ' ');
    if (text) d.text = text.slice(0, 80);
    if (el.href) d.href = String(el.href).slice(0, 200);
    if (el.dataset && el.dataset.refno) d.refno = el.dataset.refno;
    return d;
  };
  document.addEventListener('click', (e) => {
    if (e.target && e.target.closest && e.target.closest('#bidbarn-box')) return;
    const el = (e.target.closest && e.target.closest('a,button,img,input,label,select,textarea,td,li,div')) || e.target;
    send('click', {el: describe(el), x: e.clientX, y: e.clientY});
  }, true);
  let lastScroll = 0;
  window.addEventListener('scroll', () => {
    const now = Date.now();
    if (now - lastScroll < 700) return;
    lastScroll = now;
    send('scroll', {y: Math.round(window.scrollY), h: document.documentElement.scrollHeight, vh: window.innerHeight});
  }, {passive: true});
  let lastHover = {key: null, t: 0};
  document.addEventListener('mouseover', (e) => {
    if (!e.target.closest) return;
    const el = e.target.closest('a.gallery-demo, img#auction-sheet-img, div.card-item-listings, [data-refno], #bidAmountDisplay, #btnPlaceBid');
    if (!el) return;
    const d = describe(el);
    const key = d.href || d.refno || d.id || d.text;
    const now = Date.now();
    if (lastHover.key === key && now - lastHover.t < 1500) return;
    lastHover = {key: key, t: now};
    send('hover', {el: d});
  }, true);
  document.addEventListener('change', (e) => {
    const el = e.target;
    if (!el || !el.matches || el.id === 'bidbarn-reason') return;
    if (el.matches('input, select, textarea')) send('input', {el: describe(el), value: String(el.value).slice(0, 120)});
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.target && e.target.id === 'bidbarn-reason') return;
    if (['Enter', 'Escape', 'PageDown', 'PageUp', 'ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) send('key', {key: e.key});
  }, true);
  const mount = () => {
    if (!document.body || document.getElementById('bidbarn-box')) return;
    const box = document.createElement('div');
    box.id = 'bidbarn-box';
    box.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:2147483647;background:#111;color:#fff;padding:8px 10px;border-radius:8px;font:12px system-ui,sans-serif;box-shadow:0 2px 12px rgba(0,0,0,.45);width:320px;opacity:.93';
    box.innerHTML = '<div style="margin-bottom:4px">BidBarn is watching. <b>Why?</b> <span style="color:#aaa">(Enter saves, Esc clears)</span></div>'
      + '<input id="bidbarn-reason" placeholder="e.g. skip: dump body  /  bid: clean sheet, good photos" style="width:100%;box-sizing:border-box;padding:6px;border-radius:6px;border:1px solid #666;background:#222;color:#fff;font:13px system-ui,sans-serif">'
      + '<div id="bidbarn-last" style="margin-top:4px;color:#9ae6b4;min-height:14px"></div>';
    document.body.appendChild(box);
    const inp = box.querySelector('#bidbarn-reason');
    inp.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter' && inp.value.trim()) {
        send('reason', {text: inp.value.trim()});
        box.querySelector('#bidbarn-last').textContent = 'saved: ' + inp.value.trim();
        inp.value = '';
      } else if (e.key === 'Escape') { inp.value = ''; }
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
})();
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def ref_from_url(url: str) -> str | None:
    """The lot reference when `url` is an auction detail page, else None."""
    parts = urlsplit(url or "")
    if not re.search(r"(^|\.)nichibojapan\.com$", parts.hostname or "", re.I):
        return None
    m = re.fullmatch(r"/auction/([^/]+)", parts.path.rstrip("/"))
    if not m or m.group(1) in NOT_A_LOT:
        return None
    return unquote(m.group(1))


def search_chassis(url: str) -> str | None:
    parts = urlsplit(url or "")
    if parts.path.rstrip("/") not in ("/auction", "/auction/search"):
        return None
    return (parse_qs(parts.query).get("Chassis", [""])[0] or "").strip().upper() or None


class Recorder:
    """Appends events to events.jsonl and queues screenshots for the main loop."""

    def __init__(self, session_dir: Path):
        self.dir = session_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fh = (self.dir / "events.jsonl").open("a", encoding="utf-8")
        self.count = 0
        self.shots = 0
        self._queue: list[tuple[object, str, float]] = []  # (page, tag, not_before)
        self.counts: Counter = Counter()

    def record(self, type: str, **data) -> dict:
        ev = {"t": now_iso(), "type": type, **data}
        self._fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.count += 1
        self.counts[type] += 1
        return ev

    def close(self) -> None:
        self._fh.close()

    # -- browser callbacks (never call back into the page here) ---------------------
    def handle_binding(self, source, payload: str) -> None:
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            return
        kind = str(data.pop("type", "") or "page")
        self.record(kind, **data)

    def on_nav(self, page, frame) -> None:
        if frame != page.main_frame:
            return
        url = frame.url
        ref = ref_from_url(url)
        chassis = search_chassis(url)
        self.record("nav", url=url, ref=ref, chassis=chassis)
        self._queue.append((page, "lot" if ref else ("search" if chassis else "page"), time.time() + 1.2))

    def on_response(self, response) -> None:
        url = response.url
        if "/auction/bid" not in url:
            return
        q = parse_qs(urlsplit(url).query)
        text = ""
        try:
            text = response.text()[:300]
        except Exception:
            pass
        self.record("bid", url=url, ref=unquote(q.get("v", [""])[0]), yen=q.get("b", [""])[0], thousands=q.get("ba", [""])[0],
                    status=response.status, response=text)
        try:
            self._queue.append((response.frame.page, "bid", time.time() + 1.5))
        except Exception:
            pass

    def flush_screenshots(self) -> None:
        """Take the queued screenshots (called from the main loop, not from a callback)."""
        now = time.time()
        due = [q for q in self._queue if q[2] <= now]
        self._queue = [q for q in self._queue if q[2] > now]
        for page, tag, _ in due:
            self.shots += 1
            path = self.dir / f"{self.shots:04d}_{tag}.png"
            try:
                if not page.is_closed():
                    page.screenshot(path=str(path), full_page=False)
                    self.record("screenshot", file=path.name, tag=tag, url=page.url)
            except Exception as exc:  # a page mid-navigation: skip this one
                self.record("screenshot_failed", tag=tag, error=str(exc)[:120])


def session_dir(cfg: Config, stamp: datetime | None = None) -> Path:
    stamp = stamp or datetime.now()
    return cfg.db_path.parent / "sessions" / stamp.strftime("%Y%m%d-%H%M%S")


def watch(cfg: Config, echo=print) -> Path:
    """Open the bot's browser for Jim and record until he closes it (or Ctrl+C)."""
    from playwright.sync_api import sync_playwright

    out = session_dir(cfg)
    rec = Recorder(out)
    base = cfg.nichibo.get("base_url", "https://autosearch.nichibojapan.com").rstrip("/")
    with sync_playwright() as pw:
        # Same profile as the bot, but NO URL guard and NO pacing: Jim is driving, the bot only watches.
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(cfg.profile_dir), headless=False, viewport={"width": 1400, "height": 900},
            locale="en-AU", args=["--disable-blink-features=AutomationControlled"])
        ctx.expose_binding("__bidbarn_event", rec.handle_binding)
        ctx.add_init_script(INIT_SCRIPT)
        ctx.on("response", rec.on_response)

        def attach(page) -> None:
            page.on("framenavigated", lambda frame, p=page: rec.on_nav(p, frame))
            try:
                page.evaluate(INIT_SCRIPT)  # pages that already exist
            except Exception:
                pass

        ctx.on("page", attach)
        for p in ctx.pages:
            attach(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        rec.record("session", started=now_iso(), note="watch mode: the bot observes only; every bid is a manual bid")
        page.goto(base + "/auction")
        echo(f"Watching. Work on Nichibo as you normally do; type why in the box at the bottom right (Enter saves).")
        echo(f"Close the browser window when you are done. Recording to {out}")
        closed = {"v": False}
        ctx.on("close", lambda: closed.__setitem__("v", True))
        try:
            while not closed["v"]:
                rec.flush_screenshots()
                live = [p for p in ctx.pages if not p.is_closed()]
                if not live:
                    break
                try:
                    live[0].wait_for_timeout(500)
                except Exception:
                    pass
        except KeyboardInterrupt:
            echo("Stopped.")
        finally:
            rec.flush_screenshots()
            rec.record("session_end", ended=now_iso(), events=rec.count, screenshots=rec.shots)
            rec.close()
            try:
                ctx.close()
            except Exception:
                pass
    return out


# -- reading a recorded session (offline) ----------------------------------------------------------

@dataclass
class LotVisit:
    ref: str
    opened_at: str
    seconds: float | None = None
    clicks: list[str] = field(default_factory=list)
    gallery_hovers: int = 0
    sheet_viewed: bool = False
    scroll_depth: float | None = None  # 0..1 of the page height reached
    inputs: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    bid_k: str | None = None
    bid_status: str | None = None
    screenshots: list[str] = field(default_factory=list)


@dataclass
class Session:
    dir: str
    started: str | None = None
    ended: str | None = None
    searches: list[str] = field(default_factory=list)
    visits: list[LotVisit] = field(default_factory=list)
    considered_not_opened: Counter = field(default_factory=Counter)  # cards hovered on the list, never opened
    list_seconds: float = 0.0
    counts: Counter = field(default_factory=Counter)


def load_events(session: Path) -> list[dict]:
    out = []
    for ln in (session / "events.jsonl").read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    return out


def _secs(a: str, b: str) -> float:
    return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds(), 1)


def _el_label(el: dict) -> str:
    if not el:
        return "?"
    bits = [el.get("tag", "?")]
    if el.get("id"):
        bits.append("#" + el["id"])
    if el.get("text"):
        bits.append(repr(el["text"][:40]))
    elif el.get("href"):
        bits.append(el["href"][-40:])
    return " ".join(bits)


def timeline(events: list[dict], session_dir: str = "") -> Session:
    s = Session(dir=session_dir)
    current: LotVisit | None = None
    list_since: str | None = None
    opened: set[str] = set()
    for ev in events:
        kind = ev.get("type")
        s.counts[kind] += 1
        t = ev.get("t") or ""
        if kind == "session":
            s.started = ev.get("started") or t
        elif kind == "session_end":
            s.ended = ev.get("ended") or t
            if current and current.seconds is None:
                current.seconds = _secs(current.opened_at, t)
            if list_since:
                s.list_seconds += _secs(list_since, t)
        elif kind == "nav":
            if current and current.seconds is None:
                current.seconds = _secs(current.opened_at, t)
                current = None
            if list_since:
                s.list_seconds += _secs(list_since, t)
                list_since = None
            if ev.get("ref"):
                current = LotVisit(ref=ev["ref"], opened_at=t)
                s.visits.append(current)
                opened.add(ev["ref"])
            elif ev.get("chassis"):
                s.searches.append(ev["chassis"])
                list_since = t
        elif kind == "click":
            if current:
                current.clicks.append(_el_label(ev.get("el") or {}))
                el = ev.get("el") or {}
                if el.get("id") == "auction-sheet-img" or "gallery" in (el.get("cls") or ""):
                    current.sheet_viewed = current.sheet_viewed or el.get("id") == "auction-sheet-img"
        elif kind == "hover":
            el = ev.get("el") or {}
            if current:
                if el.get("id") == "auction-sheet-img":
                    current.sheet_viewed = True
                elif "gallery" in (el.get("cls") or "") or el.get("tag") == "a":
                    current.gallery_hovers += 1
            elif el.get("refno"):
                s.considered_not_opened[el["refno"]] += 1
        elif kind == "scroll" and current:
            h, vh, y = ev.get("h") or 0, ev.get("vh") or 0, ev.get("y") or 0
            if h:
                depth = min(1.0, round((y + vh) / h, 2))
                current.scroll_depth = max(current.scroll_depth or 0, depth)
        elif kind == "input" and current:
            current.inputs.append(f"{_el_label(ev.get('el') or {})} = {ev.get('value')}")
        elif kind == "reason":
            if current:
                current.reasons.append(ev.get("text") or "")
        elif kind == "bid":
            target = next((v for v in reversed(s.visits) if v.ref == ev.get("ref")), current)
            if target:
                target.bid_k = ev.get("thousands") or None
                target.bid_status = "ok" if '"success"' in (ev.get("response") or "") else f"http {ev.get('status')}"
        elif kind == "screenshot" and current:
            current.screenshots.append(ev.get("file") or "")
    for ref in opened:
        s.considered_not_opened.pop(ref, None)
    return s


def render_session(s: Session, bot_view=None) -> str:
    """Text timeline. `bot_view(ref) -> str | None` adds the bot's status for each lot when given."""
    lines = [f"Session {s.dir}: {len(s.visits)} lots opened, {sum(1 for v in s.visits if v.bid_k)} bids, "
             f"{len(s.searches)} searches ({', '.join(dict.fromkeys(s.searches)) or '-'}), "
             f"{s.list_seconds:g} s on result lists, {len(s.considered_not_opened)} cards hovered but never opened"]
    if s.started and s.ended:
        lines.append(f"   {s.started[:19]} to {s.ended[:19]}")
    secs = [v.seconds for v in s.visits if v.seconds is not None]
    if secs:
        lines.append(f"   time per lot: median {statistics.median(secs):g} s, max {max(secs):g} s; "
                     f"sheet viewed on {sum(1 for v in s.visits if v.sheet_viewed)} of {len(s.visits)}, "
                     f"photos hovered on {sum(1 for v in s.visits if v.gallery_hovers)}")
    lines.append("")
    for i, v in enumerate(s.visits, 1):
        what = []
        if v.sheet_viewed:
            what.append("sheet")
        if v.gallery_hovers:
            what.append(f"{v.gallery_hovers} photo hovers")
        if v.scroll_depth is not None:
            what.append(f"scrolled {v.scroll_depth:.0%}")
        if v.inputs:
            what.append("typed " + "; ".join(v.inputs[-2:]))
        bid = f" BID {v.bid_k}K ({v.bid_status})" if v.bid_k else ""
        bot = f" | bot: {bot_view(v.ref)}" if bot_view else ""
        secs_txt = f"{v.seconds:g} s" if v.seconds is not None else "?"
        lines.append(f"{i:3d}. {v.ref} {secs_txt}{bid} | {', '.join(what) or 'looked only'}{bot}")
        for r in v.reasons:
            lines.append(f"       why: {r}")
    if s.considered_not_opened:
        lines.append("")
        lines.append("Hovered on the list but never opened: " + ", ".join(list(s.considered_not_opened)[:20]))
    return "\n".join(lines)
