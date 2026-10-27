/* Ctrl+K / Cmd+K: find a page or a lot. Reads only: it navigates by link and asks /api/find for lots. */
(function () {
  "use strict";
  var root = document.getElementById("palette");
  if (!root) return;
  var input = document.getElementById("palette-input");
  var list = document.getElementById("palette-list");
  var PAGES = [
    ["Desk", "/"], ["Bid sheet", "/bids"], ["Guides", "/guides"], ["Rulebook", "/rulebook"],
    ["Overview and day log", "/overview"], ["Old review", "/review"], ["All lots", "/list"],
    ["Runs", "/runs"], ["Insights", "/insights"], ["Settings", "/config"], ["Sheet trial", "/sheet-trial"]
  ];
  var lots = [], active = 0, opener = null, timer = null, inflight = false, wanted = "", asked = "";

  function isOpen() { return root.getAttribute("data-open") === "true"; }

  function stateWords(l) {
    if (l.decision === "approved") return "Approved";
    if (l.decision === "rejected") return "Rejected";
    if (l.decision === "deleted") return "Cleared";
    if (l.decision === "superseded") return "Replaced by a newer scan";
    if (l.status === "SKIP") return "Skipped";
    if (l.status === "NEEDS_REVIEW") return "Waiting, needs a look";
    return "Waiting";
  }

  function items() {
    var q = input.value.trim().toLowerCase();
    var pages = PAGES.filter(function (p) { return !q || p[0].toLowerCase().indexOf(q) !== -1; })
      .map(function (p) { return { label: p[0], note: "Page", url: p[1] }; });
    var found = lots.map(function (l) {
      return { label: l.ref_no + " " + (l.summary || ""), note: stateWords(l), url: l.url };
    });
    return pages.concat(found);
  }

  function render() {
    var all = items();
    if (active >= all.length) active = Math.max(0, all.length - 1);
    list.textContent = "";
    all.forEach(function (it, i) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = it.url;
      a.className = "palette-item" + (i === active ? " is-active" : "");
      a.setAttribute("role", "option");
      a.setAttribute("aria-selected", i === active ? "true" : "false");
      var name = document.createElement("span");
      name.textContent = it.label;
      var note = document.createElement("span");
      note.className = "palette-note";
      note.textContent = it.note;
      a.appendChild(name); a.appendChild(note);
      li.appendChild(a); list.appendChild(li);
    });
    if (!all.length) {
      var none = document.createElement("li");
      none.className = "palette-empty";
      none.textContent = "Nothing found.";
      list.appendChild(none);
    }
    var cur = list.querySelector(".is-active");
    if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: "nearest" });
  }

  function ask() {
    var q = wanted;
    if (inflight || q === asked) return;
    inflight = true; asked = q;
    fetch("/api/find?q=" + encodeURIComponent(q), { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : { lots: [] }; })
      .catch(function () { return { lots: [] }; })
      .then(function (data) {
        inflight = false;
        if (q === wanted) { lots = data.lots || []; render(); }   // a stale answer is dropped
        else ask();
      });
  }

  function onInput() {
    active = 0;
    var q = input.value.trim();
    clearTimeout(timer);
    if (q.length < 2) { wanted = ""; asked = ""; lots = []; render(); return; }
    render();
    timer = setTimeout(function () { wanted = q; ask(); }, 200);
  }

  function open() {
    if (isOpen()) { input.focus(); input.select(); return; }
    opener = document.activeElement;
    root.setAttribute("data-open", "true");
    input.value = ""; lots = []; wanted = ""; asked = ""; active = 0;
    render();
    input.focus();
  }

  function close() {
    root.setAttribute("data-open", "false");
    clearTimeout(timer);
    wanted = ""; asked = "";
    if (opener && opener.focus) opener.focus();
    opener = null;
  }

  // Capture phase on window: while the palette is open no key reaches the desk's or the review's handlers.
  window.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
      e.preventDefault(); e.stopPropagation();
      open();
      return;
    }
    if (!isOpen()) return;
    e.stopPropagation();
    var n = items().length;
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); if (n) { active = (active + 1) % n; render(); } }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (n) { active = (active - 1 + n) % n; render(); } }
    else if (e.key === "Enter") {
      e.preventDefault();
      var cur = list.querySelector(".is-active");
      if (cur) window.location.href = cur.getAttribute("href");
    }
    else if (e.key === "Tab") { e.preventDefault(); input.focus(); }
  }, true);

  window.addEventListener("keyup", function (e) { if (isOpen()) e.stopPropagation(); }, true);
  window.addEventListener("keypress", function (e) { if (isOpen()) e.stopPropagation(); }, true);
  input.addEventListener("input", onInput);
  root.addEventListener("click", function (e) { if (e.target === root) { e.stopPropagation(); close(); } });
})();
