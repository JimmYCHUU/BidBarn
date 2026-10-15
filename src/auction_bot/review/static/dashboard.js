/* BidBarn dashboard: starting commands, the live console of a run, and the overview's numbers.
   The page can start only what the server's command table allows, and it decides no lot. */
(function () {
  "use strict";

  var gsap = window.gsap || null;
  var reduce = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  if (gsap) gsap.defaults({ ease: "power3.out", duration: 0.3, overwrite: "auto" });

  function one(sel, root) { return (root || document).querySelector(sel); }
  function all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function fmt(n) { return Number(n || 0).toLocaleString("en-AU"); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // -- motion: numbers count to their new value, bars grow to theirs ---------------------------
  function tweenNumber(node, to, still) {
    to = Number(to) || 0;
    var from = Number(node.dataset.value != null ? node.dataset.value : String(node.textContent).replace(/[^0-9-]/g, "")) || 0;
    node.dataset.value = String(to);
    if (!gsap || reduce || still || from === to) { node.textContent = fmt(to); return; }
    var o = { v: from };
    gsap.to(o, { v: to, duration: 0.4, ease: "power2.out", snap: { v: 1 }, onUpdate: function () { node.textContent = fmt(o.v); } });
  }
  function setShare(node, share, still) {
    share = Math.max(0, Math.min(1, Number(share) || 0));
    if (gsap && !reduce && !still) gsap.to(node, { scaleX: share, duration: 0.3 });
    else node.style.transform = "scaleX(" + share + ")";
  }

  // -- starting a command ----------------------------------------------------------------------
  var overview = one("[data-overview]");

  function showError(box, text, runId) {
    if (!box) return;
    box.textContent = text;
    if (runId) {
      box.appendChild(document.createTextNode(" "));
      var a = el("a", null, "Watch it");
      a.href = "/runs/" + runId;
      box.appendChild(a);
    }
  }

  function start(command, options, box, button) {
    if (button) button.disabled = true;
    showError(box, "");
    return fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ command: command, options: options || {} }),
    }).then(function (r) {
      return r.json().then(function (d) { return { status: r.status, d: d }; });
    }).then(function (x) {
      if (x.status === 201) {
        window.location.href = overview ? "/" : "/runs/" + x.d.id;
        return;
      }
      if (button) button.disabled = false;
      var text = (x.d && x.d.detail) || ("The command could not be started (" + x.status + ").");
      showError(box, text.charAt(0).toUpperCase() + text.slice(1) + (/[.!?]$/.test(text) ? "" : "."), x.status === 409 ? x.d.run_id : null);
    }).catch(function (err) {
      if (button) button.disabled = false;
      showError(box, "The dashboard did not answer: " + err + ". Is its window still open?");
    });
  }

  all("[data-start]").forEach(function (b) {
    b.addEventListener("click", function () {
      if (b.dataset.confirm && b.dataset.armed !== "1") return;  // review.js arms it on the first click
      var what = JSON.parse(b.dataset.start);
      var scope = b.closest(".mode, .card, section, main") || document;
      start(what.command, what.options, one("[data-start-error]", scope) || one(".form-error", scope), b);
    });
  });

  // Resume: Bid mode again in the paused session (the server joins it)
  all("[data-resume]").forEach(function (b) {
    b.addEventListener("click", function () {
      b.disabled = true;
      var box = one("[data-session-error]", b.parentNode);
      showError(box, "");
      fetch("/api/sessions/" + b.dataset.resume + "/resume", { method: "POST", headers: { Accept: "application/json" } })
        .then(function (r) { return r.json().then(function (d) { return { status: r.status, d: d }; }); })
        .then(function (x) {
          if (x.status === 201) { window.location.href = overview ? "/" : "/runs/" + x.d.id; return; }
          b.disabled = false;
          showError(box, (x.d && x.d.detail) || ("Bid mode could not be resumed (" + x.status + ")."), x.status === 409 ? x.d.run_id : null);
        }).catch(function (err) {
          b.disabled = false;
          showError(box, "The dashboard did not answer: " + err + ". Is its window still open?");
        });
    });
  });

  all("form[data-run-command]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var options = {};
      all("[data-kind]", form).forEach(function (input) {
        var kind = input.dataset.kind, v = input.value.trim();
        if (kind === "bool") options[input.name] = input.checked;
        else if (kind === "int") options[input.name] = v ? parseInt(v, 10) : null;
        else if (kind === "list") { var parts = v.split(/[\s,;]+/).filter(Boolean); options[input.name] = parts.length ? parts : null; }
        else options[input.name] = v || null;
      });
      start(form.dataset.runCommand, options, one(".form-error", form), one("button[type=submit]", form));
    });
  });

  // -- the console of one run ------------------------------------------------------------------
  var STATUS = {
    running: { word: "Running", icon: "loader-circle" },
    ok: { word: "Finished", icon: "circle-check" },
    failed: { word: "Failed", icon: "circle-x" },
    stopped: { word: "Stopped", icon: "square" },
    paused: { word: "Paused", icon: "pause" },
  };
  var PAUSABLE = ["signin", "guides", "scan", "judge"];  // the Bid stages Pause is offered at (jobs.PAUSABLE)

  function since(start, end) {
    var s = Math.max(0, Math.round(((end ? new Date(end) : new Date()) - new Date(start)) / 1000));
    if (s < 90) return s + " s";
    if (s < 5400) return Math.floor(s / 60) + " min " + ("0" + (s % 60)).slice(-2) + " s";
    return Math.floor(s / 3600) + " h " + ("0" + Math.floor((s % 3600) / 60)).slice(-2) + " min";
  }

  function lineNode(text) {
    var cls = "log-line";
    var head = text.slice(0, 24);
    if (/^STOPPED:/.test(text) || /^Traceback/.test(text) || /^\d\d:\d\d:\d\d (ERROR|CRITICAL)\b/.test(head)) cls += " is-error";
    else if (/^\d\d:\d\d:\d\d WARNING\b/.test(head)) cls += " is-warn";
    else if (/^\d\d:\d\d:\d\d \w+ == /.test(text) || /^\[\d+\/\d+\]/.test(text)) cls += " is-head";
    var node = el("div", cls);
    var m = /^(\d\d:\d\d:\d\d \w+ )(.*)$/.exec(text);
    if (m) {
      node.appendChild(el("span", "t", m[1]));
      node.appendChild(document.createTextNode(m[2]));
    } else {
      node.textContent = text;
    }
    return node;
  }

  function attach(root) {
    var id = root.dataset.runId;
    var compact = root.classList.contains("console--compact");
    var status = root.dataset.status;
    var initial = {};
    try { initial = JSON.parse(one("[data-initial]", root).textContent || "{}"); } catch (e) { initial = {}; }

    var log = one("[data-log]", root), follow = one("[data-follow]", root), note = one("[data-log-note]", root);
    var where = one("[data-where]", root), elapsed = one("[data-elapsed]", root), stop = one("[data-stop]", root);
    var pause = one("[data-pause]", root), drop = one("[data-delete-session]", root);
    var guidesBox = one("[data-guides]", root), guideRow = one("[data-guide-row]", root);
    var meter = one("[data-meter]", root), counters = one("[data-counters]", root);
    var keep = compact ? 200 : 3000;
    var queue = [], scheduled = false, dropped = 0, cells = [], timer = null;
    var first = true;  // what the page opens with is shown as it is; only changes move

    // the log: lines arrive in bursts, the page is written once per frame
    function flush() {
      scheduled = false;
      if (!queue.length) return;
      var lines = queue; queue = [];
      var quiet = lines.length > 5 || reduce;
      var frag = document.createDocumentFragment();
      lines.forEach(function (text) {
        var node = lineNode(text);
        if (!quiet) node.classList.add("is-new");
        frag.appendChild(node);
      });
      log.appendChild(frag);
      while (log.childElementCount > keep) { log.removeChild(log.firstElementChild); dropped++; }
      if (dropped && note) note.textContent = "Showing the last " + fmt(keep) + " lines.";
      if (follow.checked) log.scrollTop = log.scrollHeight;
    }
    function push(text) {
      queue.push(text);
      if (queue.length > keep) { dropped += queue.length - keep; queue.splice(0, queue.length - keep); }  // a tab left in the background
      if (scheduled) return;
      scheduled = true;
      // A frame when the tab is in view; the timer when it is not (frames stop in a hidden tab).
      var timer = window.setTimeout(function () { if (scheduled) flush(); }, 500);
      window.requestAnimationFrame(function () { window.clearTimeout(timer); if (scheduled) flush(); });
    }
    log.addEventListener("scroll", function () {
      var atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
      if (follow.checked !== atEnd) follow.checked = atEnd;
    }, { passive: true });
    follow.addEventListener("change", function () { if (follow.checked) log.scrollTop = log.scrollHeight; });

    // the numbers beside the log
    function showCounters(pairs) {
      counters.hidden = false;
      pairs.forEach(function (p) {
        var key = p[0], value = p[1];
        if (value == null) return;
        var dd = one('[data-k="' + key + '"]', counters);
        if (!dd) {
          var box = el("div", "counter");
          box.appendChild(el("dt", null, p[2]));
          dd = el("dd", null, "0");
          dd.dataset.k = key;
          box.appendChild(dd);
          counters.appendChild(box);
        }
        tweenNumber(dd, value, first);
      });
    }
    function showMeter(label, i, total) {
      if (!total) { meter.hidden = true; return; }
      meter.hidden = false;
      one("[data-meter-label]", meter).textContent = label;
      one("[data-meter-value]", meter).textContent = fmt(i) + " of " + fmt(total);
      setShare(one("[data-meter-fill]", meter), i / total, first);
    }

    // the guide row: one cell per guide, the lit one is where the scan is now
    function showGuides(p) {
      var n = Number(p.guides) || 0;
      if (!n) return;
      guidesBox.hidden = false;
      if (cells.length !== n) {
        guideRow.textContent = "";
        cells = [];
        for (var i = 0; i < n; i++) {
          var c = el("i", "g");
          guideRow.appendChild(c);
          cells.push(c);
        }
      }
      var marks = String(p.marks || ""), codes = p.codes || [], count = { f: 0, e: 0, x: 0 };
      var names = { f: "lots for review", e: "nothing new", x: "error" };
      cells.forEach(function (c, i) {
        var m = marks.charAt(i), cls = "g";
        if (m === "f") cls += " is-found"; else if (m === "e") cls += " is-empty"; else if (m === "x") cls += " is-error";
        else if (status === "running" && i === Number(p.guide) - 1) cls += " is-now";
        if (count[m] != null) count[m]++;
        if (c.className !== cls) c.className = cls;
        c.title = (codes[i] || "Guide " + (i + 1)) + (names[m] ? ": " + names[m] : (cls.indexOf("is-now") > 0 ? ": scanning now" : ""));
      });
      var rest = one("[data-legend-rest]", root);
      if (rest) {
        rest.hidden = marks.length >= n;
        one("[data-legend-rest-text]", rest).textContent = status === "running" ? "to come" : "not scanned";
      }
      one("[data-n-found]", root).textContent = count.f;
      one("[data-n-empty]", root).textContent = count.e;
      one("[data-n-error]", root).textContent = count.x;
      guideRow.setAttribute("aria-label", n + " guides: " + count.f + " with lots for review, " + count.e + " with nothing new, " +
        count.x + " with an error, " + (n - marks.length) + " to come");
    }

    // the five stages of a Bid run, drawn on the run page and the overview
    var STAGES = ["signin", "guides", "scan", "review", "place"];
    function setStages(stage) {
      var here = stage === "judge" ? "scan" : stage;
      var cur = STAGES.indexOf(here);
      all("[data-stages]").forEach(function (list) {
        list.dataset.stage = here || "";
        all("li", list).forEach(function (li, i) {
          var st = i < cur ? "done" : (i === cur ? "running" : "idle");
          li.className = "step step--" + st + (st === "running" ? " is-next" : "");
          var use = one("use", li), svg = one("svg", li);
          if (use) use.setAttribute("href", use.getAttribute("href").split("#")[0] + "#i-" + (st === "done" ? "circle-check" : (st === "running" ? "loader-circle" : "circle")));
          if (svg) svg.classList.toggle("spin", st === "running");
        });
      });
      var banner = one("[data-review-banner]");
      if (banner) banner.hidden = !(here === "review" && status === "running");
    }

    function render(p) {
      if (!p || !p.stage) return;
      if (pause) pause.hidden = PAUSABLE.indexOf(p.stage) < 0;  // at the review it waits for you anyway; a placement is not paused
      if (drop) drop.hidden = p.stage === "place";
      if (root.dataset.command === "bid" && status === "running") setStages(p.stage);  // a finished run keeps the server's stages
      if (p.stage === "scan") {
        showGuides(p);
        where.textContent = p.guide ? "Guide " + p.guide + " of " + p.guides + (p.code ? ", " + p.code : "") : "Reading the guides";
        showCounters([["cards", p.cards, "Cards read"], ["opened", p.opened, "Lots opened"],
          ["review", (p.review || 0) + (p.bid || 0), "For review"], ["skip", p.skip, "Skipped"], ["reused", p.reused, "Known already"],
          ["pending", p.pending, "Images pending"], ["model_calls", p.model_calls, "Model calls"], ["errors", p.errors, "Errors"]]);
      } else if (p.stage === "judge") {
        showMeter("Lots looked at", p.i, p.total);
        where.textContent = p.total ? "Lot " + p.i + " of " + p.total : "Nothing waits for a judgement";
        showCounters([["judged", p.judged, "Judged"], ["unavailable", p.unavailable, "Model unavailable"],
          ["pages", p.pages, "Pages opened"], ["errors", p.errors, "Errors"]]);
      } else if (p.stage === "replay") {
        showMeter("Lots checked", p.checked, p.total);
        where.textContent = p.total ? "Lot " + p.checked + " of " + p.total : "Nothing to replay";
        showCounters([["changed", p.changed, "Changed"], ["applied", p.applied, "Stored"], ["unchanged", p.unchanged, "Unchanged"],
          ["needs_fetch", p.needs_fetch, "Need a page"]]);
      } else if (p.stage === "place") {
        showMeter(p.dry_run ? "Bids typed (dry run)" : "Bids placed", p.i, p.total);
        where.textContent = p.total ? (p.ref_no ? p.ref_no + ": " + (p.message || "") : "Bid " + p.i + " of " + p.total) : "No approved bids waiting";
      } else if (p.stage === "signin") {
        where.textContent = p.waiting ? "Waiting for the sign-in in the browser window" :
          (p.nichibo && p.carbarn ? "Signed in to Nichibo and the Carbarn dashboard" : "Checking the sign-in");
      } else if (p.stage === "guides") {
        where.textContent = p.guides ? p.guides + " bidding guides fetched" : "Fetching the bidding guides";
      } else if (p.stage === "review") {
        where.textContent = p.waiting === false ? "Review finished" :
          "Waiting for your review" + (p.pending != null ? ": " + p.pending + " to review, " + (p.approved || 0) + " approved" : "");
      } else if (p.stage === "learn") {
        var phase = p.phase || "recording";
        where.textContent = phase === "recording" ? "Recording. Close the browser window when you are done. Stop abandons the session." :
          phase === "guides" ? "Fetching the bidding guides" : phase === "scan" ? "Scanning the codes you searched, for comparison" : "Comparing your session with the bot";
        if (phase === "recording") showCounters([["events", p.events, "Events"], ["lots", p.lots, "Lots"], ["bids", p.bids, "Your bids"]]);
      }
    }

    function setStatus(next) {
      status = next;
      root.dataset.status = next;
      var pill = one("[data-state]", root), info = STATUS[next] || { word: next, icon: "circle" };
      if (pill) {
        pill.className = "pill " + next;
        one("[data-state-text]", pill).textContent = info.word;
        var use = one("use", pill), svg = one("svg", pill);
        if (use) use.setAttribute("href", use.getAttribute("href").split("#")[0] + "#i-" + info.icon);
        if (svg) svg.classList.toggle("spin", next === "running");
      }
    }

    function tick() {
      if (!initial.started_at) return;
      elapsed.textContent = (status === "running" ? "running for " : "took ") + since(initial.started_at, status === "running" ? null : initial.finished_at);
    }

    function finish(d) {
      if (timer) { clearInterval(timer); timer = null; }
      initial.finished_at = new Date().toISOString();
      setStatus(d.status || "failed");
      tick();
      if (stop) stop.hidden = true;
      if (pause) pause.hidden = true;
      if (drop) drop.hidden = true;
      all(".is-now", guideRow).forEach(function (c) { c.classList.remove("is-now"); });
      var reload = function () { window.location.reload(); };
      if (gsap && !reduce && cells.length && d.status === "ok") {
        gsap.fromTo(cells, { opacity: 0.3 }, { opacity: 1, duration: 0.3, stagger: 0.012, ease: "power2.out", onComplete: reload });
      } else {
        window.setTimeout(reload, 700);
      }
    }

    function listen() {
      var es = new EventSource("/api/runs/" + id + "/events");
      es.addEventListener("log", function (e) { push(JSON.parse(e.data)); });
      es.addEventListener("progress", function (e) { render(JSON.parse(e.data)); });
      es.addEventListener("done", function (e) { es.close(); flush(); finish(JSON.parse(e.data)); });
      es.onopen = function () { if (note && !dropped) note.textContent = ""; };
      es.onerror = function () { if (note) note.textContent = "Connection lost. Trying again."; };
    }

    function history(after) {
      fetch("/api/runs/" + id + "/log?after=" + after + "&limit=2000", { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          (d.lines || []).forEach(push);
          if (!d.done && (d.lines || []).length) history(d.next);
          else if (!(d.lines || []).length && after === 0) log.dataset.empty = "This run wrote no lines.";
        }).catch(function () { if (note) note.textContent = "The log could not be read."; });
    }

    if (stop) {
      stop.addEventListener("click", function () {
        if (stop.dataset.armed !== "1") return;
        stop.disabled = true;
        fetch("/api/runs/" + id + "/cancel", { method: "POST", headers: { Accept: "application/json" } })
          .then(function (r) { return r.json().then(function (d) { return { status: r.status, d: d }; }); })
          .then(function (x) {
            stop.textContent = "Stopping";
            if (note) note.textContent = x.status === 202 ? "Stopping at the next check point." : (x.d.detail || "It could not be stopped.");
          }).catch(function () { stop.disabled = false; });
      });
    }

    if (pause) {
      pause.addEventListener("click", function () {
        pause.disabled = true;
        fetch("/api/runs/" + id + "/pause", { method: "POST", headers: { Accept: "application/json" } })
          .then(function (r) { return r.json().then(function (d) { return { status: r.status, d: d }; }); })
          .then(function (x) {
            if (x.status === 202) {
              pause.textContent = "Pausing";
              if (note) note.textContent = "Pausing at the next check point. The lots found so far stay in the review.";
            } else {
              pause.disabled = false;
              if (note) note.textContent = x.d.detail || "It could not be paused.";
            }
          }).catch(function () { pause.disabled = false; });
      });
    }

    render(initial.progress);
    first = false;
    tick();
    if (status === "running") {
      timer = window.setInterval(tick, 1000);
      listen();
    } else {
      history(0);
    }
  }

  all("[data-console]").forEach(attach);

  // -- the overview keeps its numbers current ---------------------------------------------------
  if (overview) {
    var current = overview.dataset.current || "";
    window.setInterval(function () {
      if (document.hidden) return;
      fetch("/api/status", { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (s) {
          var now = s.current ? String(s.current.id) : "";
          if (now !== current && !one("[data-console][data-status=running]")) { window.location.reload(); return; }
          // a Bid run that moved to another stage: the band and the stages line are drawn by the server
          var stage = s.current && s.current.command === "bid" ? (s.current.stage || "") : "";
          if (s.current && s.current.command === "bid" && stage !== (overview.dataset.stage || "")) { window.location.reload(); return; }
          all("[data-count]").forEach(function (node) {
            var v = s.counts[node.dataset.count];
            if (v != null) tweenNumber(node, v);
          });
        }).catch(function () {});
    }, 10000);
  }
})();
