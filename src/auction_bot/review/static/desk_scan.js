/* The desk: the scan bar. Starts a Scan now run, stops the running one, and keeps the bar current.
   The only command it can start is "scan-now". All the job traffic of the desk is here; desk.js has none.
   While a scan runs it asks the board to refresh (a "desk:refresh" event that desk.js answers). No library. */
(function () {
  "use strict";

  var host = document.getElementById("desk-scan");
  if (!host) return;

  var BOARD_EVERY_MS = 30000;   // a running scan: the board is refreshed this often
  var timer = null;             // the one poll timer
  var inFlight = false;         // never more than one request at a time
  var lastBoard = 0;            // when the board was last asked to refresh

  function bar() { return host.querySelector("[data-desk-scan]"); }
  function kind() { var b = bar(); return b ? b.dataset.kind : ""; }
  function pollMs() { var b = bar(); return Math.max(1, Number(b && b.dataset.poll) || 60) * 1000; }
  function active(k) { return k === "running" || k === "signin"; }
  function askBoard() { lastBoard = Date.now(); document.dispatchEvent(new CustomEvent("desk:refresh")); }

  function schedule() {
    clearTimeout(timer); timer = null;
    if (document.hidden) return;
    timer = setTimeout(reload, pollMs());
  }
  function show(text) {
    var t = host.querySelector(".desk-scan-text");
    if (t) t.textContent = text;
  }
  function reload() {
    clearTimeout(timer); timer = null;
    if (inFlight) { schedule(); return Promise.resolve(); }
    inFlight = true;
    var before = kind();
    return fetch("/desk/scan", { headers: { Accept: "text/html" } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
      .then(function (html) {
        host.innerHTML = html;
        var now = kind();
        if (active(before) && !active(now)) askBoard();
        else if (now === "running" && Date.now() - lastBoard >= BOARD_EVERY_MS) askBoard();
      })
      .catch(function () { show("The scan bar could not be refreshed."); })
      .then(function () { inFlight = false; schedule(); });
  }

  function send(url, body) {
    if (inFlight) return Promise.resolve();
    inFlight = true;
    clearTimeout(timer); timer = null;
    var opts = { method: "POST", headers: { Accept: "application/json" } };
    if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = body; }
    return fetch(url, opts)
      .then(function (r) {
        if (r.ok) return null;
        return r.json().catch(function () { return null; }).then(function (data) {
          return (data && data.detail && String(data.detail)) || "The request was refused (error " + r.status + ").";
        });
      })
      .catch(function () { return "The server did not answer."; })
      .then(function (problem) {
        inFlight = false;
        return reload().then(function () { if (problem) show(problem); });
      });
  }

  host.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-desk-scan-act]");
    if (!btn || !host.contains(btn) || btn.disabled) return;
    if (btn.dataset.deskScanAct === "start") {
      btn.disabled = true;
      send("/api/runs", JSON.stringify({ command: "scan-now", options: {} }));
    } else if (btn.dataset.deskScanAct === "stop") {
      var run = (bar() || {}).dataset && bar().dataset.run;
      if (!run) return;
      btn.disabled = true;
      send("/api/runs/" + run + "/cancel", null);
    }
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { clearTimeout(timer); timer = null; }
    else reload();                         // once on return, then the chain resumes
  });

  schedule();
})();
