/* BidBarn desk: selection, keys, decisions with a 10-second undo window, clear and telemetry.
   Every decision is one lot, through the review page's own routes (POST /p/{id}/decide, /delete, /event, named
   in the lot view's data-decide, data-delete and data-event). Nothing here approves anything by itself: a key or
   a click on one lot makes one decision, and it is sent only when its undo window ends. No library. */
(function () {
  "use strict";

  var UNDO_MS = 10000;          // Jim, 2026-10-26: a decision waits this long, and U or Undo takes it back
  var CLEAR_ARM_MS = 3000;      // D twice within this time clears the lot
  var CLEAR_GAP_MS = 300;       // ...but the second D must come at least this long after the first: a deliberate press
  var SETTLE_MS = 400;          // no decision for this long after a lot view is swapped in: the next lot's
                                // Approve sits under the cursor, and a double click must not decide it too
  var NOTE_MS = 6000;           // how long a plain message stays in the toast

  var board = document.getElementById("desk-board");
  var lotPane = document.getElementById("desk-lot");
  var toast = document.getElementById("desk-toast");
  if (!board || !lotPane || !toast) return;

  var state = { session: "", lane: "", row: null, lot: null, token: 0 };  // row: pid of the board row; lot: pid shown
  var queue = [];               // decisions waiting out their window, oldest first
  var told = {};                // "pid:event" already posted for this view of the lot
  var clearArmed = null;        // { pid, at } after one D
  var noteTimer = null;
  var shown = null;             // the sheet or photo that Z zooms
  var shownAt = 0;              // when the lot view on screen was swapped in (SETTLE_MS counts from here)
  var narrow = window.matchMedia("(max-width: 1279px)");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

  // -- small helpers ---------------------------------------------------------------------------
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function fmtK(n) { return Number(n).toLocaleString("en-US") + "K"; }
  function boardUrl() {
    return "/desk/board?session=" + encodeURIComponent(state.session) + "&lane=" + encodeURIComponent(state.lane);
  }
  function lotUrl(pid) { return "/desk/lot/" + encodeURIComponent(pid); }
  // The article counts only while it is the lot that was asked for: during a load the old one stays on screen,
  // and nothing may act on it.
  function lotView() {
    var v = $(".desk-lotview", lotPane);
    return v && state.lot && v.dataset.lot === state.lot ? v : null;
  }
  function refOf(v, pid) { return (v && v.dataset.ref) || ("Lot " + pid); }
  function typing(el) {
    if (!el || !el.tagName) return false;
    var t = el.tagName;
    return t === "INPUT" || t === "TEXTAREA" || t === "SELECT" || el.isContentEditable;
  }
  function waitingFor(pid) {
    return queue.some(function (q) { return q.row === String(pid) || q.pid === String(pid); });
  }
  // True just after a lot view appeared: a decision key or click then belongs to the lot before it.
  function settling() { return Date.now() - shownAt < SETTLE_MS; }

  // -- the toast: an error line that stays until dismissed or replaced, and one line for notes and the Undo ----
  var errorText = "", noteText = "";
  function undoButton(q) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "desk-toast-undo"; b.textContent = "Undo";
    b.addEventListener("click", function () { undo(q); });      // q: that row's own entry; none: the newest
    return b;
  }
  function renderToast() {
    toast.textContent = "";
    var newest = queue[queue.length - 1];
    if (errorText) {
      var e = document.createElement("div");
      e.className = "desk-toast-error";
      e.appendChild(document.createTextNode(errorText + " "));
      var d = document.createElement("button");
      d.type = "button"; d.className = "desk-toast-undo"; d.textContent = "Dismiss";
      d.addEventListener("click", function () { errorText = ""; renderToast(); });
      e.appendChild(d);
      toast.appendChild(e);
    }
    if (noteText) {
      var n = document.createElement("div");
      n.textContent = noteText;
      toast.appendChild(n);
    } else if (newest) {
      var w = document.createElement("div");
      w.appendChild(document.createTextNode(newest.label + " "));
      w.appendChild(undoButton());
      toast.appendChild(w);
    }
    toast.classList.toggle("is-on", toast.childNodes.length > 0);
  }
  function say(text) {
    clearTimeout(noteTimer);
    noteText = text;
    noteTimer = setTimeout(function () { noteText = ""; renderToast(); }, NOTE_MS);
    renderToast();
  }
  function fail(text) { errorText = text; renderToast(); }
  function showWaiting() {         // a decision now waits: its line replaces any note, a sticky error stays
    clearTimeout(noteTimer); noteText = ""; renderToast();
  }

  // -- board: session and lane, rows ---------------------------------------------------------------
  function rowFor(pid) { return $('.desk-row[data-pid="' + pid + '"]', board); }
  function laneRows() {
    return $$(".desk-row", board).filter(function (r) {
      return r.dataset.session === state.session && r.dataset.lane === state.lane;
    });
  }
  function readBoardState() {
    var s = $(".desk-sess[aria-current]", board), l = $(".desk-lane[aria-current]", board);
    if (s) state.session = s.dataset.session;
    if (l) state.lane = l.dataset.lane;
  }
  function setCurrent(a, on) { if (on) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current"); }
  function markWaiting(q) {
    var r = rowFor(q.row);
    if (!r) return;
    r.classList.add("desk-row--waiting");
    if (r.parentNode && !$(".desk-pending", r.parentNode)) {
      var s = document.createElement("span");
      s.className = "desk-pending";
      s.appendChild(document.createTextNode(q.label + " "));
      s.appendChild(undoButton(q));
      r.parentNode.appendChild(s);
    }
  }
  function paintBoard() {
    $$(".desk-row", board).forEach(function (r) {
      var show = r.dataset.session === state.session && r.dataset.lane === state.lane;
      if (r.parentNode) r.parentNode.hidden = !show;   // the <li>: .desk-row{display:grid} would beat `hidden` on the button
      r.hidden = !show;
      r.removeAttribute("aria-current");
      r.classList.remove("desk-row--waiting");
    });
    $$(".desk-pending", board).forEach(function (n) { n.remove(); });
    $$(".desk-sess", board).forEach(function (a) { setCurrent(a, a.dataset.session === state.session); });
    $$(".desk-lane", board).forEach(function (a) { setCurrent(a, a.dataset.lane === state.lane); });
    queue.forEach(markWaiting);
    var r = state.row && rowFor(state.row);
    if (r && !r.hidden) r.setAttribute("aria-current", "true");
  }
  function refreshBoard() {
    return fetch(boardUrl(), { headers: { Accept: "text/html" } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
      .then(function (html) { board.innerHTML = html; readBoardState(); paintBoard(); autoOpen(); })
      .catch(function () { say("The board could not be refreshed. Reload the page."); });
  }
  function switchTo(session, lane) {
    state.session = session; state.lane = lane;
    dropLot();                // the lot on screen may belong to another lane
    paintBoard();             // at once, from the rows already here
    refreshBoard();           // then from the server, so the counts and notes are current
  }

  // -- selection -----------------------------------------------------------------------------------
  function loadLot(pid, rowPid) {
    var token = ++state.token;
    state.lot = String(pid);
    if (rowPid !== undefined) state.row = String(rowPid);
    shown = null;
    lotPane.setAttribute("aria-busy", "true");
    paintBoard();
    return fetch(lotUrl(pid), { headers: { Accept: "text/html" } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
      .then(function (html) {
        if (token !== state.token) return;      // a newer selection won
        lotPane.innerHTML = html;
        shownAt = Date.now();
        lotPane.removeAttribute("aria-busy");
        lotPane.scrollTop = 0;
        told = {};
        if (lotView()) telemetry("open", "desk");
      })
      .catch(function () {
        if (token !== state.token) return;
        lotPane.innerHTML = ""; lotPane.removeAttribute("aria-busy");     // nothing stays to act on
        fail("Lot " + pid + " could not be opened.");
      });
  }
  function select(pid) {
    if (!pid) return Promise.resolve();
    var r = rowFor(pid);
    if (r) { r.focus({ preventScroll: true }); r.scrollIntoView({ block: "nearest", behavior: "auto" }); }
    return loadLot(pid, pid);
  }
  function neighbour(pid, step) {
    var rows = laneRows().filter(function (r) { return r.dataset.pid === String(pid) || !waitingFor(r.dataset.pid); });
    var i = rows.findIndex(function (r) { return r.dataset.pid === String(pid); });
    if (i < 0) return step > 0 ? (rows[0] && rows[0].dataset.pid) : (rows[rows.length - 1] && rows[rows.length - 1].dataset.pid);
    var n = rows[i + step];
    return n ? n.dataset.pid : null;
  }
  function nextAfter(pid) { return neighbour(pid, 1) || neighbour(pid, -1); }
  function dropLot() {
    state.token++;
    state.lot = null; shown = null;
    lotPane.innerHTML = ""; lotPane.removeAttribute("aria-busy");
  }
  function selectRow(pid) {       // narrow: only the selection moves, Enter opens the lot
    dropLot();
    state.row = String(pid);
    paintBoard();
    var r = rowFor(pid);
    if (r) { r.focus({ preventScroll: true }); r.scrollIntoView({ block: "nearest", behavior: "auto" }); }
  }
  function move(step) {
    var to = neighbour(state.row, step);
    if (!to) return;
    if (narrow.matches) selectRow(to); else select(to);
  }
  function closeLot() {
    dropLot();
    state.row = null;
    paintBoard();
  }
  function autoOpen() {           // wide layouts only: the first lot of the lane shows beside the list
    if (narrow.matches || state.lot) return;
    var first = laneRows().filter(function (r) { return !waitingFor(r.dataset.pid); })[0];
    if (first) select(first.dataset.pid);
  }

  // -- telemetry: what Jim looked at, through the existing event route ------------------------------
  function telemetry(name, value) {
    var v = lotView();
    if (!v) return;
    var key = v.dataset.lot + ":" + name;
    if (told[key]) return;
    told[key] = true;
    fetch(v.dataset.event, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ event: name, value: value == null ? null : String(value) }),
      keepalive: true,
    }).catch(function () {});
  }

  // -- evidence: look at, sheet, photos --------------------------------------------------------------
  function flash(el) {
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: reduced.matches ? "auto" : "smooth" });
    el.classList.add("desk-flash");
    setTimeout(function () { el.classList.remove("desk-flash"); }, 1600);
  }
  function inLot(sel) {            // only inside the lot that is loaded; none while another lot loads
    var v = lotView();
    return v ? $(sel, v) : null;
  }
  function showSheet() {
    if (!lotView()) { say("Opening the lot..."); return; }
    var a = inLot('[data-desk-zoom="sheet"]');
    if (!a) { say("This lot has no sheet downloaded."); return; }
    shown = a; flash(a);
  }
  function showPhoto(n) {
    if (!lotView()) { say("Opening the lot..."); return; }
    var a = inLot('[data-desk-zoom="photo"][data-photo-no="' + n + '"]');
    if (!a) { say("This lot has no photo " + n + "."); return; }
    shown = a; flash(a); telemetry("photos_click", n);
  }
  function showSection(label) { flash(inLot('[aria-label="' + label + '"]')); }
  function look(btn) {
    var where = btn.dataset.where;
    if (where === "photo" && btn.dataset.photo) showPhoto(btn.dataset.photo);
    else if (where === "sheet") showSheet();
    else if (where === "grid") showSection("Amount");
    else if (where === "pair") showSection("Import eligibility");
    else showSection("Requirements");
  }
  function zoom() {
    if (!lotView()) { say("Opening the lot..."); return; }
    var a = shown && shown.isConnected ? shown : inLot('[data-desk-zoom="sheet"]');
    if (a) a.click(); else say("Nothing to zoom on this lot.");
  }
  function openNichibo() {
    if (!lotView()) { say("Opening the lot..."); return; }
    var a = inLot('[data-desk-ev="nichibo_click"]');
    if (a) a.click(); else say("This lot has no Nichibo address.");
  }

  // -- panels ---------------------------------------------------------------------------------------
  function panel(name) { return $('[data-panel="' + name + '"]', lotPane); }
  function openPanel(name) {
    ["amount", "reject"].forEach(function (n) { var p = panel(n); if (p) p.hidden = n !== name; });
    var field = name === "amount" ? $('input[name="amount"]', lotPane) : $('textarea[name="reason"]', lotPane);
    if (field) field.focus();
  }
  function closePanels() {
    var open = false;
    ["amount", "reject"].forEach(function (n) { var p = panel(n); if (p && !p.hidden) { p.hidden = true; open = true; } });
    if (open && typing(document.activeElement)) document.activeElement.blur();
    return open;
  }

  // -- decisions: queued, then sent when the window ends -------------------------------------------
  function queueDecision(action, amount, extra) {
    var v = lotView();
    if (!v) return;
    var pid = v.dataset.lot, row = state.row || pid;
    if (waitingFor(row) || waitingFor(pid)) { say("This lot already waits to be sent. Undo it first."); return; }
    if (action === "approve" && v.dataset.covered) { say(v.dataset.covered + " It is not approved again."); return; }
    var body = new FormData();
    body.append("action", action);
    body.append("via", "desk");
    if (action === "approve") body.append("amount", String(amount));
    Object.keys(extra || {}).forEach(function (k) { if (extra[k] !== "") body.append(k, extra[k]); });
    var q = {
      pid: String(pid), ref: refOf(v, pid), row: String(row), url: v.dataset.decide, body: body, session: state.session, lane: state.lane,
      label: action === "approve" ? "Approved " + fmtK(amount) + " ·" : "Rejected ·", timer: null,
    };
    q.timer = setTimeout(function () { send(q, true); }, UNDO_MS);
    queue.push(q);
    closePanels();
    markWaiting(q);
    showWaiting();
    var next = nextAfter(q.row);
    if (next) select(next); else closeLot();
  }
  function approve() {
    if (settling()) return;
    var v = lotView();
    if (v && v.dataset.covered) { say(v.dataset.covered + " It is not approved again."); return; }   // one bid per vehicle
    var b = v && $('[data-act="approve"]', v);
    if (!b) return;
    if (b.disabled) { say(b.title || "This lot cannot be approved as it is."); return; }
    var amt = Number(b.dataset.deskAmount);
    if (!(amt > 0)) { say("There is no amount to approve. Change the amount."); return; }
    queueDecision("approve", amt);
  }
  function chosenAmount() {
    var own = $('input[name="amount"]', lotPane);
    if (own && own.value.trim() !== "") return Number(own.value);     // an own amount wins
    var pick = $('input[name="pick"]:checked', lotPane);
    return pick ? Number(pick.value) : 0;
  }
  function approveChosen() {
    if (settling()) return;
    var v = lotView();
    if (!v) return;
    var own = $('input[name="amount"]', v);
    if (own && own.value.trim() !== "" && !/^\d+$/.test(own.value.trim())) { say("Type a whole number of thousands of yen."); return; }
    var amt = chosenAmount();
    if (!(amt > 0)) { say("Type or pick an amount first."); return; }
    if (v.dataset.start && amt < Number(v.dataset.start)) { say("The amount is below the start price (" + fmtK(v.dataset.start) + ")."); return; }
    queueDecision("approve", amt);
  }
  function rejectWithReason() {
    if (settling()) return;
    var v = lotView();
    if (!v) return;
    var reason = $('textarea[name="reason"]', v).value.trim();
    if (!reason) { say("Say why, in a few words."); $('textarea[name="reason"]', v).focus(); return; }
    var rule = $('input[name="make_rule"]', v);
    queueDecision("reject", 0, {
      reason: reason,
      make_rule: rule && rule.checked ? "1" : "",
      rule_text: $('input[name="rule_text"]', v).value.trim(),
      feedback_req: $('select[name="feedback_req"]', v).value,
      feedback_note: $('input[name="feedback_note"]', v).value.trim(),
    });
  }
  function describeError(r, data, ref) {
    if (r.status === 409) return ref + ": This lot changed while you decided. It is back on the desk.";
    return ref + ": " + ((data && data.detail && String(data.detail)) || "The decision was not accepted (" + r.status + ").");
  }
  function send(q, refresh) {
    clearTimeout(q.timer);
    var i = queue.indexOf(q);
    if (i >= 0) queue.splice(i, 1);
    var p = fetch(q.url, { method: "POST", body: q.body, headers: { Accept: "application/json" }, keepalive: true });
    if (!refresh) return p.catch(function () {});     // leaving the page: nothing more to do
    return p.then(function (r) {
      if (r.ok) return refreshBoard().then(showWaiting);
      return r.json().catch(function () { return null; }).then(function (data) {
        return refreshBoard().then(function () { fail(describeError(r, data, q.ref)); });
      });
    }).catch(function () {
      refreshBoard().then(function () { fail(q.ref + ": The decision could not be sent. The lot is back on the desk."); });
    });
  }
  function undo(q) {              // a row's own Undo passes its entry; U and the toast cancel the newest
    q = q || queue[queue.length - 1];
    var i = q ? queue.indexOf(q) : -1;
    if (i < 0) { say("Nothing to undo."); return; }
    queue.splice(i, 1);
    clearTimeout(q.timer);
    state.session = q.session; state.lane = q.lane;
    renderToast();
    paintBoard();
    select(q.row);
  }
  function sendAllNow() {       // the page is going away: nothing waiting may be lost
    queue.slice().forEach(function (q) { send(q, false); });
  }

  // -- clear: out of the desk without a decision, no undo window ---------------------------------------
  function clearLot() {
    if (settling()) return;
    var v = lotView();
    if (!v || !$('[data-act="clear"]', v)) return;
    var pid = v.dataset.lot, row = state.row || pid;
    if (waitingFor(row) || waitingFor(pid)) { say("This lot already waits to be sent. Undo it first."); return; }
    var now = Date.now();
    if (!clearArmed || clearArmed.pid !== pid || now - clearArmed.at > CLEAR_ARM_MS) {
      clearArmed = { pid: pid, at: now };
      say("Press D again to clear this lot. Cleared is not a decision.");
      return;
    }
    if (now - clearArmed.at < CLEAR_GAP_MS) return;   // too soon after the first to be a second, deliberate press
    clearArmed = null;
    var next = nextAfter(row), ref = refOf(v, pid);
    fetch(v.dataset["delete"], { method: "POST", headers: { Accept: "application/json" } })
      .then(function (r) {
        return r.json().catch(function () { return null; }).then(function (data) {
          if (!r.ok) {
            var why = r.status === 409 ? "it is no longer waiting." : ((data && data.detail && String(data.detail)) || "error " + r.status + ".");
            return refreshBoard().then(function () { fail(ref + " could not be cleared: " + why); });
          }
          return refreshBoard().then(function () { say("Cleared, not a decision."); if (next) select(next); else closeLot(); });
        });
      })
      .catch(function () { fail(ref + " could not be cleared: the server did not answer."); });
  }

  // -- keys --------------------------------------------------------------------------------------------
  var HELP = "J/K or arrows move · Enter opens · A approve · E amount · R reject · D D clear · U undo · O Nichibo · S sheet · 1-9 photo · Z zoom · Esc closes";
  function onKey(e) {
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    var t = e.target;
    var moves = /^(j|k|ArrowUp|ArrowDown)$/i.test(e.key);
    if (e.repeat && !moves) return;       // a held key never decides, clears or undoes a second lot
    if (typing(t)) {
      if (e.key === "Escape") closePanels();
      else if (e.key === "Enter" && t.tagName === "INPUT" && t.closest('[data-panel="amount"]')) { e.preventDefault(); approveChosen(); }
      return;
    }
    var k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    var handled = true;
    if (k === "j" || k === "ArrowDown") move(1);
    else if (k === "k" || k === "ArrowUp") move(-1);
    else if (k === "Enter") {
      if (t.closest && t.closest("button, a")) return;     // a focused button or link keeps its own Enter
      if (state.row && !lotView()) loadLot(state.row, state.row);
    }
    else if (k === "a") approve();
    else if (k === "e") { var cv = lotView(); if (cv && cv.dataset.covered) say(cv.dataset.covered); else if (cv && panel("amount")) openPanel("amount"); }
    else if (k === "r") { if (lotView() && panel("reject")) openPanel("reject"); }
    else if (k === "d") clearLot();
    else if (k === "u") undo();
    else if (k === "o") openNichibo();
    else if (k === "s") showSheet();
    else if (k === "z") zoom();
    else if (k === "?") say(HELP);
    else if (/^[1-9]$/.test(k)) showPhoto(k);
    else if (k === "Escape") {
      if (!closePanels() && narrow.matches && state.lot) closeLot();
      clearArmed = null;
    } else handled = false;
    if (handled) e.preventDefault();
  }

  // -- clicks ------------------------------------------------------------------------------------------
  var DECIDING = { approve: true, "amount-submit": true, "reject-submit": true, clear: true };
  function onBoardClick(e) {
    var link = e.target.closest(".desk-sess, .desk-lane");
    if (link) {
      e.preventDefault();
      var session = link.dataset.session || state.session;
      var lane = link.dataset.lane || state.lane;
      if (link.dataset.session) {      // a session link: its URL names the lane to open it on
        var m = /[?&]lane=([^&]+)/.exec(link.getAttribute("href") || "");
        lane = m ? decodeURIComponent(m[1]) : state.lane;
      }
      switchTo(session, lane);
      return;
    }
    var row = e.target.closest(".desk-row");
    if (row) select(row.dataset.pid);
  }
  function onLotClick(e) {
    var t = e.target;
    if (t.closest('button[data-act="close"]')) { closeLot(); return; }
    if (!lotView()) return;               // the lot on screen is not the one chosen yet
    var btn = t.closest("button[data-act]");
    if (btn) {
      var act = btn.dataset.act;
      // The second click of a double click (e.detail 2, 3...) never decides or clears: by then the next lot's
      // bar may sit under the cursor. A keyboard press on a focused button has e.detail 0 and still counts.
      if (e.detail > 1 && DECIDING[act]) return;
      if (act === "approve") approve();
      else if (act === "amount") openPanel("amount");
      else if (act === "reject") openPanel("reject");
      else if (act === "clear") clearLot();
      else if (act === "amount-submit") approveChosen();
      else if (act === "reject-submit") rejectWithReason();
      return;
    }
    var lk = t.closest("button.desk-look");
    if (lk) { look(lk); return; }
    var sib = t.closest("button[data-sibling]");
    if (sib) { loadLot(sib.dataset.sibling); return; }
    var z = t.closest("[data-desk-zoom]");
    if (z) {       // the link itself opens the picture; this only records the look
      shown = z;
      if (z.dataset.deskZoom === "sheet") telemetry("sheet_zoom", "click");
      else telemetry("photos_click", z.dataset.photoNo || "click");
      return;
    }
    if (t.closest('[data-desk-ev="nichibo_click"]')) telemetry("nichibo_click", "link");
  }
  function onLotInput(e) {
    var t = e.target;
    if (t.name === "amount") {
      $$('input[name="pick"]', lotPane).forEach(function (r) { r.checked = false; });   // a typed amount wins
      telemetry("amount_edit", "typed");
    } else if (t.name === "pick") {
      var own = $('input[name="amount"]', lotPane);
      if (own) own.value = t.value;
    }
  }

  board.addEventListener("click", onBoardClick);
  lotPane.addEventListener("click", onLotClick);
  lotPane.addEventListener("input", onLotInput);
  lotPane.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.name === "reason") { e.preventDefault(); rejectWithReason(); }
  });
  document.addEventListener("keydown", onKey);
  document.addEventListener("click", function (e) {     // narrow: a click outside the lot overlay closes it
    var t = e.target;
    if (!t.isConnected || (e.composedPath && e.composedPath().indexOf(toast) >= 0)) return;   // the toast's own buttons
    if (narrow.matches && state.lot && !lotPane.contains(t) && !t.closest("#desk-toast, .desk-row, .desk-pending, .desk-sess, .desk-lane")) closeLot();
  });
  window.addEventListener("pagehide", sendAllNow);

  readBoardState();
  paintBoard();
  autoOpen();
})();
