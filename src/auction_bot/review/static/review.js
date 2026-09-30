/* BidBarn review pages: keyboard triage, the sheet lightbox, what-you-looked-at events and the
   in-place decision row. Every decision still goes through POST /p/{id}/decide, one lot at a
   time, and only after the reviewer confirms it. */
(function () {
  "use strict";

  function post(url, body, json) {
    return fetch(url, {
      method: "POST",
      headers: json ? { "Content-Type": "application/json", Accept: "application/json" } : { Accept: "application/json" },
      body: json ? JSON.stringify(body) : body,
      keepalive: true,
    });
  }
  function event(pid, name, value) {
    if (!pid) return;
    post("/p/" + pid + "/event", { event: name, value: value == null ? null : String(value) }, true).catch(function () {});
  }

  // -- confirm in place: the first click arms the button, the second one acts -----------------
  // The button is given the width of its longer label, so arming it moves nothing.
  document.querySelectorAll("[data-confirm]").forEach(function (b) {
    var label = b.innerHTML, width = b.offsetWidth;
    b.textContent = b.dataset.confirm;
    width = Math.max(width, b.offsetWidth);
    b.innerHTML = label;
    if (width) b.style.minWidth = width + "px";
  });
  var armTimer = null;
  function disarm(b) {
    if (!b || b.dataset.armed !== "1") return;
    b.dataset.armed = "";
    b.innerHTML = b.dataset.label;
    b.classList.remove("is-armed");
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-confirm]");
    if (!b || b.disabled) return;
    if (b.dataset.armed === "1") { clearTimeout(armTimer); return; }
    e.preventDefault();
    b.dataset.label = b.innerHTML;
    b.dataset.armed = "1";
    b.textContent = b.dataset.confirm;
    b.classList.add("is-armed");
    clearTimeout(armTimer);
    armTimer = setTimeout(function () { disarm(b); }, 5000);
  });

  // -- lightbox for the sheet -----------------------------------------------------------------
  var box = document.getElementById("lightbox");
  function sheetOpen() { return !!box && box.dataset.open === "true"; }
  function showSheet(src, pid) {
    if (!src || !box) return;
    var img = box.querySelector("img");
    if (!img) {
      img = document.createElement("img");
      img.alt = "Full size image";
      box.appendChild(img);
    }
    img.src = src;
    box.dataset.open = "true";
    event(pid, "sheet_zoom", null);
  }
  function hideSheet() { if (box) box.dataset.open = "false"; }
  if (box) box.addEventListener("click", hideSheet);

  // -- hooks shared by the pages --------------------------------------------------------------
  var pagePid = document.body.dataset.pid;
  document.querySelectorAll("[data-ev]").forEach(function (el) {
    el.addEventListener("click", function () {
      var row = el.closest("tr.lot");
      event(row ? row.dataset.pid : pagePid, el.dataset.ev, null);
    });
  });
  document.querySelectorAll("[data-sheet-zoom]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      showSheet(a.getAttribute("href"), pagePid);
    });
  });
  var amount = document.querySelector("form.decide input[name=amount]");
  if (amount && pagePid) amount.addEventListener("change", function () { event(pagePid, "amount_edit", amount.value); });
  var approveBtn = document.querySelector("form.decide button.approve");

  // -- triage view ----------------------------------------------------------------------------
  var rows = Array.prototype.slice.call(document.querySelectorAll("tr.lot"));
  if (!rows.length) {
    if (pagePid) document.addEventListener("keydown", detailKeys);
    return;
  }
  var idx = -1;
  var panel = null;

  function select(i, log, still) {
    if (i < 0 || i >= rows.length) return;
    if (idx >= 0) rows[idx].classList.remove("is-selected");
    if (panel && panel.row !== rows[i]) closePanel();
    idx = i;
    var r = rows[idx];
    r.classList.add("is-selected");
    if (!still) r.scrollIntoView({ block: "nearest" });
    if (log) event(r.dataset.pid, "open", "triage");
  }
  rows.forEach(function (r, i) {
    r.addEventListener("click", function (e) { if (!e.target.closest("a,button,img,input")) select(i, true); });
    var img = r.querySelector("img.thumb");
    if (img) img.addEventListener("click", function () { select(i, false); showSheet(r.dataset.sheet, r.dataset.pid); });
    r.querySelectorAll("button.act").forEach(function (b) {
      b.addEventListener("click", function () { select(i, false); openPanel(r, b.dataset.act, null, false); });
    });
  });

  function mark(r, text, ok) {
    var out = r.querySelector(".result");
    if (out) {
      out.textContent = text;
      out.classList.toggle("is-ok", !!ok);
      out.classList.toggle("is-bad", !ok);
    }
    if (ok) {
      r.classList.add("is-done");
      r.querySelectorAll("button.act").forEach(function (b) { b.disabled = true; });
    }
  }

  function closePanel() {
    if (!panel) return;
    panel.tr.remove();
    panel = null;
  }

  // what an approval does on this PC today, said once, in the row
  function approveHint() {
    var lock = document.body.dataset.lock;
    if (lock === "testing") return "Recorded for testing, nothing is submitted. ";
    if (lock === "dry") return "Dry run, nothing is submitted. ";
    return "";
  }

  function openPanel(r, act, focus, byKey) {
    closePanel();
    if (r.classList.contains("is-done")) return;
    var pid = r.dataset.pid;
    var bid = r.dataset.bid || "";  // empty when the guide gave no amount: the amount is then Jim's to type

    var tr = document.createElement("tr");
    tr.className = "decide-row";
    tr.setAttribute("role", "row");
    var td = document.createElement("td");
    td.setAttribute("role", "cell");
    td.colSpan = r.children.length;
    var form = document.createElement("form");
    form.className = "decide-inline" + (byKey ? " by-key" : "");
    form.noValidate = true;

    if (act === "approve") {
      form.innerHTML =
        '<div class="field field--narrow"><label for="amt-' + pid + '">Bid amount (K JPY)</label>' +
        '<input id="amt-' + pid + '" name="amount" type="number" min="0" step="10" inputmode="numeric"></div>' +
        '<button class="btn--primary" type="submit">Approve</button>' +
        '<button type="button" class="btn--quiet" data-cancel>Cancel</button>' +
        '<span class="faint">' + (bid ? "" : "No grid amount for this lot: type the amount you want. ") + approveHint() + '<kbd>Enter</kbd> approves, <kbd>Esc</kbd> cancels</span>' +
        '<div class="error" role="alert"></div>';
      var input = form.elements.amount;
      var go = form.querySelector("button[type=submit]");
      var relabel = function () {
        var n = parseInt(input.value, 10);
        go.textContent = n > 0 ? "Approve " + n.toLocaleString() + "K" : "Approve";
        go.disabled = !(n > 0);
      };
      input.value = bid;
      input.addEventListener("input", relabel);
      relabel();
    } else {
      form.innerHTML =
        '<div class="field"><label for="why-' + pid + '">Reason for rejecting</label>' +
        '<input id="why-' + pid + '" name="reason" type="text" autocomplete="off">' +
        '<span class="help">Saved as evidence. For example: 10 seats on the sheet.</span></div>' +
        '<button class="btn--danger" type="submit">Reject</button>' +
        '<button type="button" class="btn--quiet" data-cancel>Cancel</button>' +
        '<div class="error" role="alert"></div>';
    }

    td.appendChild(form);
    tr.appendChild(td);
    r.after(tr);
    panel = { tr: tr, row: r, form: form, act: act };

    form.addEventListener("submit", function (e) { e.preventDefault(); submitDecision(r, act, form); });
    form.querySelector("[data-cancel]").addEventListener("click", closePanel);

    var target = act === "reject" || focus === "amount" ? form.querySelector("input") : form.querySelector("button[type=submit]");
    target.focus({ preventScroll: true });
    if (target.select) target.select();
    tr.scrollIntoView({ block: "nearest" });
  }

  function fail(form, text) {
    var out = form.querySelector(".error");
    if (out) out.textContent = text;
  }

  function submitDecision(r, act, form) {
    var pid = r.dataset.pid;
    var body = new URLSearchParams();
    body.set("action", act);
    body.set("via", "triage");
    if (act === "approve") {
      var n = parseInt(form.elements.amount.value, 10);
      if (!(n > 0)) { fail(form, "Enter the amount in K JPY."); form.elements.amount.focus(); return; }
      if (String(n) !== String(r.dataset.bid)) {
        event(pid, "amount_edit", n);
        r.dataset.bid = String(n);
        var shown = r.querySelector(".bid");
        if (shown) shown.textContent = n.toLocaleString();
      }
      body.set("amount", String(n));
    } else {
      var reason = form.elements.reason.value.trim();
      if (!reason) { fail(form, "A reason is needed to reject."); form.elements.reason.focus(); return; }
      body.set("reason", reason);
    }
    form.querySelectorAll("button, input").forEach(function (el) { el.disabled = true; });

    post("/p/" + pid + "/decide", body, false).then(function (resp) {
      return resp.json().then(function (d) { return { status: resp.status, d: d }; });
    }).then(function (x) {
      if (x.status === 200 && x.d.ok) {
        closePanel();
        mark(r, x.d.decision + (x.d.amount ? " " + Number(x.d.amount).toLocaleString() + "K" : ""), true);
        var next = rows.indexOf(r) + 1;
        if (next < rows.length) select(next, true);
      } else {
        form.querySelectorAll("button, input").forEach(function (el) { el.disabled = false; });
        fail(form, x.d.detail || ("Error " + x.status));
      }
    }).catch(function (err) {
      form.querySelectorAll("button, input").forEach(function (el) { el.disabled = false; });
      fail(form, String(err));
    });
  }

  // the keyboard list is a popover under the page head; a key opens it, so it does not animate
  var keysPop = document.getElementById("keys"), keysBtn = document.querySelector("[data-keys-toggle]");
  function help(force) {
    if (!keysPop) return;
    var open = force != null ? force : keysPop.hidden;
    keysPop.hidden = !open;
    if (keysBtn) keysBtn.setAttribute("aria-expanded", open ? "true" : "false");
  }
  if (keysBtn) keysBtn.addEventListener("click", function () { help(); });
  document.addEventListener("click", function (e) {
    if (keysPop && !keysPop.hidden && !e.target.closest("#keys, [data-keys-toggle]")) help(false);
  });

  function triageKeys(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (sheetOpen()) { hideSheet(); e.preventDefault(); return; }
    if (keysPop && !keysPop.hidden && e.key === "Escape") { help(false); e.preventDefault(); return; }
    if (panel) {
      if (e.key === "Escape") { var back = panel.row; closePanel(); select(rows.indexOf(back), false); e.preventDefault(); }
      return;
    }
    if (e.target.closest("input,textarea,select")) return;
    var r = idx >= 0 ? rows[idx] : null;
    switch (e.key) {
      case "j": select(idx + 1, true); break;
      case "k": select(idx - 1, true); break;
      case "a": if (r) openPanel(r, "approve", null, true); break;
      case "r": if (r) openPanel(r, "reject", null, true); break;
      case "e": if (r) openPanel(r, "approve", "amount", true); break;
      case "z": if (r) showSheet(r.dataset.sheet, r.dataset.pid); break;
      case "o": if (r) { event(r.dataset.pid, "nichibo_click", null); window.open(r.dataset.url, "_blank"); } break;
      case "Enter": if (r && !e.target.closest("a,button")) window.location.href = "/p/" + r.dataset.pid; else return; break;
      case "?": help(); break;
      default: return;
    }
    e.preventDefault();
  }

  function detailKeys(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (sheetOpen()) { hideSheet(); e.preventDefault(); return; }
    if (e.target.closest("input,textarea,select")) return;
    if (e.key === "r") { var t = document.querySelector("form.decide textarea[name=reason]"); if (t) { t.focus(); e.preventDefault(); } }
    if (e.key === "a" && approveBtn && !approveBtn.disabled) { approveBtn.focus(); approveBtn.click(); e.preventDefault(); }
    if (e.key === "o") { var a = document.querySelector("a[data-ev=nichibo_click]"); if (a) { a.click(); e.preventDefault(); } }
    if (e.key === "n") { var nx = document.querySelector("a[data-next]"); if (nx) { window.location.href = nx.href; e.preventDefault(); } }
  }

  document.addEventListener("keydown", triageKeys);
  select(0, false, true);  // the page opens at its top, whatever the height of the first lot
})();
