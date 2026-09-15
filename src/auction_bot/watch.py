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

# Injected into every Nichibo and Carbarn dashboard page (the dashboard tab may live on a
# second monitor; it is the same browser session, so it is recorded the same way). Sends
# events through window.__bidbarn_event and mounts the reason box. Nothing here clicks,
# submits or changes anything on the page.
INIT_SCRIPT = r"""
(() => {
  if (window.__bidbarnWatching) return;
  const site = /(^|\.)nichibojapan\.com$/i.test(location.hostname) ? 'nichibo'
             : /(^|\.)carbarn\.com\.au$/i.test(location.hostname) ? 'dashboard' : null;
  if (!site) return;
  window.__bidbarnWatching = true;
  const send = (type, data) => {
    try { window.__bidbarn_event(JSON.stringify(Object.assign({type: type, site: site, url: location.href, t_page: Date.now()}, data || {}))); } catch (e) {}
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
    if (site === 'dashboard') {
      // Where on the dashboard: the table row and the nearest heading (the guide's name).
      const row = el.closest && el.closest('tr');
      if (row && row !== el) d.row = (row.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80);
      let n = el;
      while (n) {
        const h = n.querySelector && n.querySelector('h1,h2,h3,h4');
        if (h && h.innerText && h.innerText.trim()) { d.heading = h.innerText.trim().replace(/\s+/g, ' ').slice(0, 60); break; }
        if (n === document.body) break;
        n = n.parentElement;
      }
    }
    return d;
  };
  // Where the mouse is, about once a second while it moves (both sites, both monitors).
  let lastMove = {t: 0, x: 0, y: 0, key: ''};
  document.addEventListener('mousemove', (e) => {
    const now = Date.now();
    if (now - lastMove.t < 1000) return;
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const d = describe(el);
    const key = (d.id || '') + '|' + (d.text || '') + '|' + (d.row || '');
    if (key === lastMove.key && Math.abs(e.clientX - lastMove.x) + Math.abs(e.clientY - lastMove.y) < 40) return;
    lastMove = {t: now, x: e.clientX, y: e.clientY, key: key};
    send('pointer', {x: e.clientX, y: e.clientY, el: d});
  }, {passive: true});
  // Text highlighted with the mouse.
  let selTimer = null, lastSel = '';
  document.addEventListener('selectionchange', () => {
    clearTimeout(selTimer);
    selTimer = setTimeout(() => {
      const text = String((window.getSelection && window.getSelection()) || '').trim().replace(/\s+/g, ' ').slice(0, 160);
      if (text && text !== lastSel) { lastSel = text; send('select', {text: text}); }
    }, 600);
  });
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
  const hoverSelector = site === 'nichibo'
    ? 'a.gallery-demo, img#auction-sheet-img, div.card-item-listings, [data-refno], #bidAmountDisplay, #btnPlaceBid'
    : 'td, th, a, button, label, input, select, [role=button], [class*=chip], [class*=badge], [class*=tag]';
  document.addEventListener('mouseover', (e) => {
    if (!e.target.closest) return;
    const el = e.target.closest(hoverSelector);
    if (!el) return;
    const d = describe(el);
    const key = d.href || d.refno || d.id || ((d.text || '') + '|' + (d.row || ''));
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
    box.style.cssText = 'position:fixed;right:16px;top:16px;z-index:2147483647;background:#1d4ed8;color:#fff;padding:10px 12px;border-radius:10px;font:13px system-ui,sans-serif;box-shadow:0 4px 18px rgba(0,0,0,.5);width:340px;border:2px solid #fff';
    box.innerHTML = '<div style="margin-bottom:6px;font-weight:700">BidBarn is watching' + (site === 'dashboard' ? ' the dashboard' : '') + '. Why? <span style="font-weight:400;opacity:.85">(Enter saves, Esc clears)</span></div>'
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
  setInterval(mount, 2000);  // the site redraws parts of the page; keep the box there
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
        self._last_nav: dict[int, tuple[str, float]] = {}  # per tab: (url, when) to drop duplicates
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

    def on_new_page(self, page) -> None:
        """A tab opened from a link or window.open has often finished its first navigation
        before we hear about it, so record where it already is."""
        url = page.url
        if url and url != "about:blank":
            self._nav(page, url, opened="new tab")

    def on_nav(self, page, frame) -> None:
        if frame != page.main_frame:
            return
        self._nav(page, frame.url)

    def _nav(self, page, url: str, **extra) -> None:
        # A new tab can be reported twice (on attach and by its own navigation event).
        last = self._last_nav.get(id(page))
        if last and last[0] == url and time.time() - last[1] < 2.0:
            return
        self._last_nav[id(page)] = (url, time.time())
        ref = ref_from_url(url)
        chassis = search_chassis(url)
        self.record("nav", url=url, ref=ref, chassis=chassis, **extra)
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
            user_data_dir=str(cfg.profile_dir), headless=False, no_viewport=True,  # the page fills the real window
            locale="en-AU", args=["--disable-blink-features=AutomationControlled", "--start-maximized"])
        ctx.expose_binding("__bidbarn_event", rec.handle_binding)
        ctx.add_init_script(INIT_SCRIPT)
        ctx.on("response", rec.on_response)

        def attach(page) -> None:
            page.on("framenavigated", lambda frame, p=page: rec.on_nav(p, frame))
            rec.on_new_page(page)  # a tab opened from a link may already be on its page
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
class Dashboard:
    """What happened on the Carbarn dashboard tab (guide reading)."""
    active_seconds: int = 0  # distinct seconds with mouse, hover, click or selection activity
    headings: Counter = field(default_factory=Counter)  # guide names under the cursor
    cells: Counter = field(default_factory=Counter)  # table cells hovered, with their row
    clicks: list[str] = field(default_factory=list)
    selections: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


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
    dashboard: Dashboard = field(default_factory=Dashboard)


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
    """Per-lot visits from the event log.

    Events sent by the page's script (clicks, hovers, reasons, inputs) can reach the log a
    little after the navigation events written directly by the recorder, so each of them
    is attributed to the lot named in its own URL, not to whichever lot was logged last.
    """
    s = Session(dir=session_dir)
    # Pass 1: navigations define the visits and the time on result lists.
    current: LotVisit | None = None
    list_since: str | None = None
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
            elif ev.get("chassis"):
                s.searches.append(ev["chassis"])
                list_since = t

    def visit_for(ev: dict) -> LotVisit | None:
        ref = ref_from_url(ev.get("url") or "")
        if ref:
            return next((v for v in reversed(s.visits) if v.ref == ref), None)
        return None

    # Pass 2: what happened on each lot, and on the dashboard.
    dash = s.dashboard
    dash_seconds: set[str] = set()
    for ev in events:
        kind = ev.get("type")
        el = ev.get("el") or {}
        if ev.get("site") == "dashboard":
            if kind in ("pointer", "hover", "click", "scroll", "select", "input"):
                dash_seconds.add((ev.get("t") or "")[:19])
            if kind in ("pointer", "hover"):
                if el.get("heading"):
                    dash.headings[el["heading"]] += 1
                if el.get("tag") in ("td", "th") and el.get("text"):
                    dash.cells[f"{el.get('row') or ''} > {el['text']}"[:100]] += 1
            elif kind == "click":
                dash.clicks.append(_el_label(el) + (f" [{el['heading']}]" if el.get("heading") else ""))
            elif kind == "select":
                dash.selections.append(ev.get("text") or "")
            elif kind == "reason":
                dash.reasons.append(ev.get("text") or "")
            continue
        v = visit_for(ev)
        if kind == "click" and v:
            v.clicks.append(_el_label(el))
            if el.get("id") == "auction-sheet-img":
                v.sheet_viewed = True
        elif kind == "hover":
            if v:
                if el.get("id") == "auction-sheet-img":
                    v.sheet_viewed = True
                elif "gallery" in (el.get("cls") or "") or el.get("tag") == "a":
                    v.gallery_hovers += 1
            elif el.get("refno"):
                s.considered_not_opened[el["refno"]] += 1
        elif kind == "scroll" and v:
            h, vh, y = ev.get("h") or 0, ev.get("vh") or 0, ev.get("y") or 0
            if h:
                v.scroll_depth = max(v.scroll_depth or 0, min(1.0, round((y + vh) / h, 2)))
        elif kind == "input" and v:
            v.inputs.append(f"{_el_label(el)} = {ev.get('value')}")
        elif kind == "reason" and v:
            v.reasons.append(ev.get("text") or "")
        elif kind == "bid":
            target = next((x for x in reversed(s.visits) if x.ref == ev.get("ref")), None)
            if target:
                target.bid_k = ev.get("thousands") or None
                target.bid_status = "ok" if '"success"' in (ev.get("response") or "") else f"http {ev.get('status')}"
        elif kind == "screenshot":
            target = visit_for(ev)
            if target:
                target.screenshots.append(ev.get("file") or "")
    for v in s.visits:
        s.considered_not_opened.pop(v.ref, None)
    dash.active_seconds = len(dash_seconds)
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
    d = s.dashboard
    if d.active_seconds or d.headings or d.clicks or d.selections:
        lines.append("")
        lines.append(f"Carbarn dashboard: about {d.active_seconds} s of activity")
        if d.headings:
            lines.append("   guides under the cursor: " + ", ".join(f"{h} ({n})" for h, n in d.headings.most_common(8)))
        if d.cells:
            lines.append("   cells looked at: " + "; ".join(f"{c} ({n})" for c, n in d.cells.most_common(8)))
        for c in d.clicks[:10]:
            lines.append(f"   click: {c}")
        for t in d.selections[:10]:
            lines.append(f"   highlighted: {t}")
        for r in d.reasons:
            lines.append(f"   why: {r}")
    return "\n".join(lines)
