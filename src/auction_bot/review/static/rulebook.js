/* The Rulebook page. A setting question: Preview starts a Rulebook preview run and shows what it would change,
   and Apply sends that very preview back to the server, which checks it again. A confirm question: Keep and
   Ask for a change post their form. It talks only to /api/runs, /api/runs/<id> and the question's own
   /rulebook/<id>/apply, /keep and /request routes. No library. */
(function () {
  "use strict";

  var POLL_MS = 1500;
  var STATUS_WORDS = { BID: "Bid", NEEDS_REVIEW: "Review", SKIP: "Skip" };

  function num(n) { return Number(n).toLocaleString("en-US"); }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  function fail(detail, status) {
    return (detail && String(detail)) || "The request was refused (error " + status + ").";
  }
  // Every route answers JSON when asked: { ok, ... } or { detail } with a status.
  function parse(r) {
    return r.json().catch(function () { return {}; }).then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
  }
  function down() { return { ok: false, status: 0, body: { detail: "The server did not answer." } }; }

  function outcome(lot) {
    var a = lot.after || {}, b = lot.before || {};
    if (a.status === "SKIP" && b.status !== "SKIP") return "skip";
    if (typeof a.bid_jpy_k === "number" && typeof b.bid_jpy_k === "number") {
      if (a.bid_jpy_k < b.bid_jpy_k) return "drop";
      if (a.bid_jpy_k > b.bid_jpy_k) return "rise";
    }
    if (a.judge_pending === true && b.judge_pending !== true) return "fresh";
    if (a.proposed_bid_jpy_k !== b.proposed_bid_jpy_k) return "proposed";
    return "other";
  }

  // "4 lots change: 3 amounts drop, 1 lot now skips." or "No stored lot would change."
  function countSentence(summary) {
    var lots = Array.isArray(summary.lots) ? summary.lots : [];
    var total = Math.max(lots.length, typeof summary.lots_total === "number" ? summary.lots_total : 0);
    if (!total) return "No stored lot would change.";
    var n = { drop: 0, rise: 0, skip: 0, fresh: 0, proposed: 0, other: 0 };
    lots.forEach(function (lot) { n[outcome(lot)] += 1; });
    var parts = [];
    if (n.drop) parts.push(n.drop === 1 ? "1 amount drops" : n.drop + " amounts drop");
    if (n.rise) parts.push(n.rise === 1 ? "1 amount rises" : n.rise + " amounts rise");
    if (n.skip) parts.push(n.skip === 1 ? "1 lot now skips" : n.skip + " lots now skip");
    if (n.fresh) parts.push(n.fresh === 1 ? "1 lot needs a fresh judgement" : n.fresh + " lots need a fresh judgement");
    if (n.proposed) parts.push(n.proposed === 1 ? "1 proposed amount changes" : n.proposed + " proposed amounts change");
    if (n.other) parts.push(n.other === 1 ? "1 lot changes in another way" : n.other + " lots change in another way");
    var head = total === 1 ? "1 lot changes" : total + " lots change";
    if (total > lots.length) {
      return head + ". The table shows the first " + num(lots.length) + (parts.length ? ". Of the first " + num(lots.length) + ": " + parts.join(", ") : "") + ".";
    }
    return head + (parts.length ? ": " + parts.join(", ") : "") + ".";
  }

  function driftSentence(summary) {
    var n = typeof summary.drift === "number" ? summary.drift : 0;
    if (n < 1) return "";
    return (n === 1 ? "1 other stored lot is" : n + " other stored lots are") +
      " already out of date with today's rules; Apply stores " + (n === 1 ? "that change" : "those changes") + " too.";
  }

  function state(s) {
    var word = STATUS_WORDS[s && s.status] || (s && s.status) || "";
    s = s || {};
    var out = typeof s.bid_jpy_k === "number" ? word + " " + num(s.bid_jpy_k) + "K" : word;
    if (typeof s.proposed_bid_jpy_k === "number" && s.proposed_bid_jpy_k !== s.bid_jpy_k) out += ", proposed " + num(s.proposed_bid_jpy_k) + "K";
    if (s.judge_pending === true) out += ", judgement pending";
    return out;
  }

  function lotsTable(lots) {
    var wrap = document.createElement("div"); wrap.className = "table-wrap";
    var t = document.createElement("table");
    var head = t.createTHead().insertRow();
    ["Lot", "Before", "After"].forEach(function (h) { var th = document.createElement("th"); th.textContent = h; head.appendChild(th); });
    var body = t.createTBody();
    lots.forEach(function (lot) {
      var tr = body.insertRow();
      tr.insertCell().textContent = (lot.ref_no ? lot.ref_no + " " : "") + (lot.summary || lot.model_code || "");
      tr.insertCell().textContent = state(lot.before);
      tr.insertCell().textContent = state(lot.after);
    });
    wrap.appendChild(t);
    return wrap;
  }

  function Question(root) {
    var qid = root.dataset.qid;
    var setting = root.querySelector("[data-setting]").dataset.setting;
    var result = root.querySelector("[data-result]");
    var previewBtn = root.querySelector('[data-act="preview"]');
    var applyBtn = root.querySelector('[data-act="apply"]');
    var token = 0;          // a new choice or a new preview makes every older answer stale
    var good = null;        // { run, value } of the finished, clean preview that Apply may send

    function chosen() {
      var r = root.querySelector('input[type="radio"]:checked');
      return r ? Number(r.value) : null;
    }
    function say(text, kind, extra) {
      result.textContent = "";
      result.className = "rb-result" + (kind ? " rb-result--" + kind : "");
      if (text) { var p = document.createElement("p"); p.textContent = text; result.appendChild(p); }
      if (extra) result.appendChild(extra);
    }
    function lock(on) {   // the choice cannot change while Apply is in flight, so its reply is never dropped
      root.querySelectorAll('input[type="radio"]').forEach(function (r) { r.disabled = on; });
    }
    function reset() {
      token += 1; good = null;
      applyBtn.disabled = true; previewBtn.disabled = false;
      say("", "");
    }

    function show(run, value, mine) {
      if (mine !== token) return;
      var s = run.summary || {};
      var errors = typeof s.errors_total === "number" ? s.errors_total : (Array.isArray(s.errors) ? s.errors.length : 0);
      previewBtn.disabled = false;
      if (run.status !== "ok") {
        say("The preview did not finish. " + (run.error ? String(run.error) : "Look at its run page.") + " Preview again.", "bad");
        return;
      }
      var lots = Array.isArray(s.lots) ? s.lots : [];
      var text = countSentence(s);
      var drift = driftSentence(s);
      if (drift) text += " " + drift;
      if (errors) {
        say(text + " The preview could not check " + plural(errors, "lot", "lots") + ", so it cannot be applied. Look at its run page.", "bad",
            lots.length ? lotsTable(lots) : null);
        return;
      }
      if (!s.answer || s.answer[setting] !== value || typeof s.rulebook_version !== "number") {
        say("This preview cannot be checked against the Rulebook. Preview again.", "bad");
        return;
      }
      good = { run: run.id, value: value };
      applyBtn.disabled = false;
      say(text + " Nothing is stored until you press Apply.", "", lots.length ? lotsTable(lots) : null);
    }

    function poll(id, value, mine) {
      if (mine !== token) return;
      fetch("/api/runs/" + id, { headers: { Accept: "application/json" } })
        .then(parse, down)
        .then(function (res) {
          if (mine !== token) return;
          if (!res.ok) { previewBtn.disabled = false; say(fail(res.body.detail, res.status), "bad"); return; }
          if (res.body.status === "running") { setTimeout(function () { poll(id, value, mine); }, POLL_MS); return; }
          show(res.body, value, mine);
        });
    }

    previewBtn.addEventListener("click", function () {
      var value = chosen();
      if (value === null) { say("Choose a value to preview.", "bad"); return; }
      reset();
      var mine = token;
      previewBtn.disabled = true;
      say("Previewing. The stored lots are checked again with this answer; nothing is saved.", "");
      fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ command: "rulebook-preview", options: { question: qid, value: value } })
      }).then(parse, down).then(function (res) {
        if (mine !== token) return;
        if (!res.ok) { previewBtn.disabled = false; say(fail(res.body.detail, res.status), "bad"); return; }
        poll(res.body.id, value, mine);
      });
    });

    applyBtn.addEventListener("click", function () {
      if (!good || applyBtn.disabled) return;
      var sent = good, mine = token;
      applyBtn.disabled = true; previewBtn.disabled = true; lock(true);
      var form = new URLSearchParams();
      form.set("value", String(sent.value));
      form.set("preview_run", String(sent.run));
      fetch("/rulebook/" + qid + "/apply", {
        method: "POST", headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
        body: form.toString()
      }).then(parse, down).then(function (res) {
        if (mine !== token) return;
        lock(false);
        if (!res.ok) { previewBtn.disabled = false; say(fail(res.body.detail, res.status), "bad"); return; }
        var b = res.body;
        say(b.busy ? b.busy : "Saved as version " + b.version + ". The waiting lots are being checked again.", b.busy ? "bad" : "ok");
        setTimeout(function () { location.reload(); }, b.busy ? 6000 : 2500);
      });
    });

    root.addEventListener("change", function (e) {
      if (e.target && e.target.type === "radio") reset();
    });
  }

  function Forms(root) {
    var qid = root.dataset.qid;
    var msg = root.querySelector("[data-msg]");
    function note(text, bad) { msg.textContent = text; msg.className = "rb-msg" + (bad ? " rb-msg--bad" : ""); }
    function done(res, ok) {
      if (!res.ok) { note(fail(res.body.detail, res.status), true); return false; }
      note(ok, false);
      setTimeout(function () { location.reload(); }, 600);
      return true;
    }
    root.querySelector('[data-form="keep"]').addEventListener("submit", function (e) {
      e.preventDefault();
      fetch("/rulebook/" + qid + "/keep", { method: "POST", headers: { Accept: "application/json" } })
        .then(parse, down).then(function (res) { done(res, "Kept as it is."); });
    });
    root.querySelector('[data-form="request"]').addEventListener("submit", function (e) {
      e.preventDefault();
      var box = e.target.querySelector("textarea");
      var form = new URLSearchParams();
      form.set("text", box.value);
      fetch("/rulebook/" + qid + "/request", {
        method: "POST", headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
        body: form.toString()
      }).then(parse, down).then(function (res) { done(res, "Your request is saved."); });
    });
  }

  document.querySelectorAll(".rb-q").forEach(function (root) {
    if (root.querySelector("[data-setting]")) Question(root);
    else if (root.querySelector("[data-form]")) Forms(root);
  });
})();
