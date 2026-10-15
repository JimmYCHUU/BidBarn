"""The two-click confirm in review.js, run under Node against a stub page (no browser, no network).

A page can carry several confirm buttons (Approve and Delete on a lot, Delete and Finish on the review),
so arming one must disarm the other: a button left armed would act on a single click."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REVIEW_JS = Path(__file__).resolve().parents[1] / "src" / "auction_bot" / "review" / "static" / "review.js"

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const handlers = {};
function button(label, confirm) {
  const b = { innerHTML: label, dataset: { confirm: confirm }, offsetWidth: 40, style: {}, disabled: false,
    classList: { names: new Set(), add(c) { this.names.add(c); }, remove(c) { this.names.delete(c); } },
    closest(sel) { return sel === "[data-confirm]" ? this : null; } };
  Object.defineProperty(b, "textContent", { get() { return this.innerHTML; }, set(v) { this.innerHTML = v; } });
  return b;
}
const approve = button("Approve bid", "Confirm approve"), del = button("Delete", "Confirm delete");
globalThis.window = {};
globalThis.document = {
  body: { dataset: {} },
  querySelectorAll(sel) { return sel === "[data-confirm]" ? [approve, del] : []; },
  querySelector() { return null; },
  getElementById() { return null; },
  addEventListener(type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
};
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
function click(b) {
  const e = { target: b, prevented: false, preventDefault() { this.prevented = true; } };
  (handlers.click || []).forEach(function (h) { h(e); });
  return e.prevented;
}
const out = {};
out.first_click_held = click(approve);
out.approve_armed = approve.dataset.armed === "1" && approve.innerHTML === "Confirm approve";
out.delete_click_held = click(del);
out.approve_after_delete = { armed: approve.dataset.armed === "1", label: approve.innerHTML };
out.delete_armed = del.dataset.armed === "1";
out.approve_single_click_held = click(approve);  // one click on a disarmed Approve only arms it again
console.log(JSON.stringify(out));
process.exit(0);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_arming_one_confirm_button_disarms_the_other(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    run = subprocess.run([shutil.which("node"), str(harness), str(REVIEW_JS)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out["first_click_held"] and out["approve_armed"]  # the first click only arms Approve
    assert out["delete_click_held"] and out["delete_armed"]
    assert out["approve_after_delete"] == {"armed": False, "label": "Approve bid"}  # arming Delete disarmed Approve
    assert out["approve_single_click_held"]  # so a single click on Approve does not approve
