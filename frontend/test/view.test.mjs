import { test } from "node:test";
import assert from "node:assert/strict";
import { parseHTML } from "linkedom";
import { gaps, segments } from "../dist/model.js";
import { renderCase } from "../dist/view.js";
const rows = [
  { period: "1991-09-01", score: 350, bin: 7, market: 1 },
  { period: "1991-11-01", score: 250, bin: 5, market: 1 },
];
test("calendar gap splits line and retains observations without zero-fill", () => {
  assert.deepEqual(gaps(rows), ["1991-10"]);
  assert.equal(segments(rows).length, 2);
  assert.equal(rows.length, 2);
});
test("evidence is safe text, statuses stay separate and sparse chart does not bridge gap", () => {
  const { document } = parseHTML("<html><body><main></main></body></html>");
  globalThis.document = document;
  const root = document.querySelector("main");
  renderCase(root, {
    case_id: "demo",
    account_ids: [1000003],
    as_of_period: "1991-12-01",
    revision: 2,
    workflow_status: "held",
    verification_status: "incomplete",
    analyst_decision: "pending",
    snapshot_hash: "abc",
    package_hash: null,
    created_at: "date",
    updated_at: "date",
    evidence: rows,
    policy_memos: [
      {
        source_file: "POL-001",
        sha256: "hash",
        text: "<img src=x onerror=alert(1)>",
      },
    ],
    limitations: ["Coverage is limited."],
    analysis: null,
  });
  assert.equal(root.querySelectorAll("img").length, 0);
  assert.match(root.textContent, /<img src=x/);
  assert.match(root.textContent, /held/);
  assert.match(root.textContent, /incomplete/);
  assert.match(root.textContent, /pending/);
  assert.match(root.textContent, /1991-10/);
  assert.equal(root.querySelectorAll("polyline").length, 0);
  assert.equal(root.querySelectorAll("circle").length, 2);
  assert.match(root.textContent, /Coverage is limited/);
});
test("empty history has explicit absence, no invented chart", () => {
  const { document } = parseHTML("<main></main>");
  globalThis.document = document;
  const root = document.querySelector("main");
  renderCase(root, {
    account_ids: [1],
    as_of_period: "1991-12-01",
    policy_memos: [],
    evidence: [],
  });
  assert.equal(root.querySelector("svg"), null);
  assert.match(root.textContent, /No captured observations/);
});
