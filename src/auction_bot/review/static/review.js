/* BidBarn review page: keyboard triage, sheet lightbox, and what-you-looked-at events.
   Every decision still goes through POST /p/{id}/decide, one lot at a time. */
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

  // -- lightbox for the sheet ------------------------------------------------------
  var box = null;
  function showSheet(src, pid) {
    if (!src) return;
    if (!box) {
      box = document.createElement("div");
      box.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.85);display:flex;align-items:center;justify-content:center;z-index:99;cursor:zoom-out;overflow:auto";
      box.innerHTML = '<img style="max-width:98vw;max-height:98vh;object-fit:contain">';
      box.addEventListener("click", function () { box.style.display = "none"; });
      document.body.appendChild(box);
    }
    box.querySelector("img").src = src;
    box.style.display = "flex";
    event(pid, "sheet_zoom", null);
  }
  function hideSheet() { if (box) box.style.display = "none"; }

  // -- detail page hooks ----------------------------------------------------------------
  var pagePid = document.body.dataset.pid;
  document.querySelectorAll("[data-ev]").forEach(function (el) {
    el.addEventListener("click", function () {
      var row = el.closest("tr.lot");
      event(row ? row.dataset.pid : pagePid, el.dataset.ev, null);
    });
  });
  var amount = document.querySelector("form.decide input[name=amount]");
  if (amount && pagePid) amount.addEventListener("change", function () { event(pagePid, "amount_edit", amount.value); });
  var approveBtn = document.querySelector("form.decide button.approve");
  if (approveBtn) approveBtn.addEventListener("click", function (e) {
    if (!confirm("Approve this bid?")) e.preventDefault();
  });

  // -- triage view --------------------------------------------------------------------------
  var rows = Array.prototype.slice.call(document.querySelectorAll("tr.lot"));
  if (!rows.length) {
    if (pagePid) document.addEventListener("keydown", detailKeys);
    return;
  }
  var idx = -1;
  function select(i, fromKey) {
    if (i < 0 || i >= rows.length) return;
    if (idx >= 0) rows[idx].style.outline = "";
    idx = i;
    var r = rows[idx];
    r.style.outline = "2px solid #3730a3";
    r.scrollIntoView({ block: "nearest" });
    if (fromKey) event(r.dataset.pid, "open", "triage");
  }
  rows.forEach(function (r, i) {
    r.addEventListener("click", function (e) { if (!e.target.closest("a,button,img")) select(i, true); });
    var img = r.querySelector("img.thumb");
    if (img) img.addEventListener("click", function () { select(i, false); showSheet(r.dataset.sheet, r.dataset.pid); });
    r.querySelectorAll("button.act").forEach(function (b) {
      b.addEventListener("click", function () { select(i, false); decide(r, b.dataset.act); });
    });
  });

  function mark(r, text, ok) {
    var out = r.querySelector(".result");
    if (out) { out.textContent = text; out.style.color = ok ? "#1a7f37" : "#b3261e"; }
    if (ok) {
      r.style.opacity = "0.55";
      r.querySelectorAll("button.act").forEach(function (b) { b.disabled = true; });
    }
  }
  function decide(r, act) {
    var pid = r.dataset.pid;
    var body = new URLSearchParams();
    body.set("action", act);
    body.set("via", "triage");
    if (act === "approve") {
      var amt = r.dataset.bid;
      if (!amt) { mark(r, "no bid amount: open the detail page", false); return; }
      if (!confirm("Approve " + Number(amt).toLocaleString() + "K JPY for this lot?")) return;
      body.set("amount", amt);
    } else {
      var reason = prompt("Reason for rejecting (saved as evidence):");
      if (!reason || !reason.trim()) return;
      body.set("reason", reason.trim());
    }
    post("/p/" + pid + "/decide", body, false).then(function (resp) {
      return resp.json().then(function (d) { return { status: resp.status, d: d }; });
    }).then(function (x) {
      if (x.status === 200 && x.d.ok) {
        mark(r, x.d.decision + (x.d.amount ? " " + Number(x.d.amount).toLocaleString() + "K" : ""), true);
        var next = rows.indexOf(r) + 1;
        if (next < rows.length) select(next, true);
      } else {
        mark(r, x.d.detail || ("error " + x.status), false);
      }
    }).catch(function (err) { mark(r, String(err), false); });
  }
  function editAmount(r) {
    var v = prompt("Bid amount in K JPY (the approve key uses this):", r.dataset.bid || "");
    if (v == null) return;
    var n = parseInt(v.replace(/[^0-9]/g, ""), 10);
    if (!n) return;
    r.dataset.bid = String(n);
    var b = r.querySelector(".bid");
    if (b) b.textContent = n.toLocaleString();
    event(r.dataset.pid, "amount_edit", n);
  }
  function help() {
    alert("j / k  next / previous lot\na  approve the selected lot (asks first)\nr  reject with a reason\ne  change the amount\nz  show the sheet (any key or click closes it)\no  open the lot on Nichibo\np  open the photos on Nichibo\nEnter  detail page\n?  this help");
  }
  function triageKeys(e) {
    if (e.target.closest("input,textarea,select") || e.ctrlKey || e.metaKey || e.altKey) return;
    if (box && box.style.display === "flex" && e.key !== "z") { hideSheet(); return; }
    var r = idx >= 0 ? rows[idx] : null;
    switch (e.key) {
      case "j": select(idx + 1, true); break;
      case "k": select(idx - 1, true); break;
      case "a": if (r) decide(r, "approve"); break;
      case "r": if (r) decide(r, "reject"); break;
      case "e": if (r) editAmount(r); break;
      case "z": if (r) { if (box && box.style.display === "flex") hideSheet(); else showSheet(r.dataset.sheet, r.dataset.pid); } break;
      case "o": if (r) { event(r.dataset.pid, "nichibo_click", null); window.open(r.dataset.url, "_blank"); } break;
      case "p": if (r) { event(r.dataset.pid, "photos_click", null); window.open(r.dataset.url, "_blank"); } break;
      case "Enter": if (r) window.location.href = "/p/" + r.dataset.pid; break;
      case "?": help(); break;
      default: return;
    }
    e.preventDefault();
  }
  function detailKeys(e) {
    if (e.target.closest("input,textarea,select") || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "r") { var t = document.querySelector("form.decide textarea[name=reason]"); if (t) { t.focus(); e.preventDefault(); } }
    if (e.key === "a" && approveBtn && !approveBtn.disabled) { approveBtn.click(); e.preventDefault(); }
    if (e.key === "o") { var a = document.querySelector("a[data-ev=nichibo_click]"); if (a) { a.click(); e.preventDefault(); } }
    if (e.key === "n") { var nx = document.querySelector("a[data-next]"); if (nx) { window.location.href = nx.href; e.preventDefault(); } }
  }
  document.addEventListener("keydown", triageKeys);
  select(0, false);
})();
