import { test } from "node:test";
import assert from "node:assert/strict";
import { parseHTML } from "linkedom";
import { mountReview } from "../dist/review.js";
const sample = {
  case_id: "case",
  revision: 2,
  package_hash: "a".repeat(64),
  workflow_status: "awaiting_review",
  verification_status: "passed",
  analyst_decision: "pending",
};
function setup(c = sample, submit = async () => {}) {
  const { document } = parseHTML("<main></main>");
  globalThis.document = document;
  const root = document.querySelector("main");
  mountReview(root, c, submit);
  return {
    root,
    form: root.querySelector("form"),
    choice: root.querySelector("select"),
    reason: root.querySelector("textarea"),
    button: root.querySelector("button"),
  };
}
for (const decision of ["approved", "rejected"])
  test(`${decision} binds displayed revision/hash and prevents duplicate submission`, async () => {
    const calls = [];
    let finish;
    const ui = setup(sample, (body) => {
      calls.push(body);
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    ui.choice.querySelector(`option[value="${decision}"]`).selected = true;
    ui.reason.value = "  Reviewed evidence  ";
    ui.reason.oninput();
    assert.equal(ui.button.disabled, false);
    const first = ui.form.onsubmit({ preventDefault() {} });
    await ui.form.onsubmit({ preventDefault() {} });
    assert.deepEqual(calls, [
      {
        expected_revision: 2,
        package_hash: sample.package_hash,
        decision,
        reason: "Reviewed evidence",
      },
    ]);
    assert.equal(ui.reason.disabled, true);
    finish();
    await first;
  });
test("requires meaningful reason, bounded length and explicit decision", () => {
  const ui = setup();
  ui.reason.value = "reviewed";
  ui.reason.oninput();
  assert.equal(ui.button.disabled, true);
  ui.choice.querySelector('option[value="approved"]').selected = true;
  ui.reason.value = "   ";
  ui.reason.oninput();
  assert.equal(ui.button.disabled, true);
  ui.reason.value = "x".repeat(1001);
  ui.reason.oninput();
  assert.equal(ui.button.disabled, true);
});
for (const patch of [
  { workflow_status: "held" },
  { verification_status: "blocked" },
  { verification_status: "incomplete" },
  { workflow_status: "queued" },
  { workflow_status: "running" },
  { workflow_status: "failed" },
  { workflow_status: "completed", analyst_decision: "approved" },
  { package_hash: null },
])
  test(`unavailable state ${JSON.stringify(patch)} has no decision form`, () =>
    assert.equal(setup({ ...sample, ...patch }).form, null));
for (const message of [
  "Decision conflict or stale package.",
  "Failed to fetch",
])
  test(`${message} locks form until reload without retry`, async () => {
    let calls = 0;
    const ui = setup(sample, async () => {
      calls++;
      throw Error(message);
    });
    ui.choice.querySelector('option[value="approved"]').selected = true;
    ui.reason.value = "reviewed";
    ui.reason.oninput();
    await ui.form.onsubmit({ preventDefault() {} });
    await ui.form.onsubmit({ preventDefault() {} });
    assert.equal(calls, 1);
    assert.match(ui.root.textContent, /Refresh case/);
    assert.equal(ui.button.disabled, true);
  });
