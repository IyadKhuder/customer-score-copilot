import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseHTML } from "linkedom";
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const sample = {
  case_id: "demo",
  account_ids: [1000003],
  as_of_period: "1991-12-01",
  revision: 2,
  workflow_status: "awaiting_review",
  verification_status: "passed",
  analyst_decision: "pending",
  evidence: [],
  policy_memos: [],
};
test("controller sends bearer only in headers, supports reads, clears private UI on disconnect and ignores late responses", async () => {
  const { document } = parseHTML(
    readFileSync(new URL("../index.html", import.meta.url), "utf8"),
  );
  globalThis.document = document;
  const calls = [];
  let late;
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options });
    if (path.startsWith("/cases?"))
      return {
        status: 200,
        ok: true,
        json: async () => ({ items: [sample], has_more: false }),
      };
    return new Promise((resolve) => {
      late = () => resolve({ status: 200, ok: true, json: async () => sample });
    });
  };
  await import("../dist/app.js?controller");
  const input = document.getElementById("token");
  input.value = "disposable-token-at-least-32-characters";
  document.getElementById("connect").onsubmit({ preventDefault() {} });
  await flush();
  await flush();
  assert.equal(input.value, "");
  assert.equal(document.getElementById("workspace").hidden, false);
  assert.equal(
    calls[0].options.headers.Authorization,
    "Bearer disposable-token-at-least-32-characters",
  );
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[0].options.redirect, "error");
  assert.ok(!calls[0].path.includes("token"));
  document.querySelector("#queue button").onclick();
  await flush();
  document.getElementById("disconnect").onclick();
  late();
  await flush();
  await flush();
  assert.equal(document.getElementById("workspace").hidden, true);
  assert.equal(document.querySelectorAll("#queue button").length, 0);
  assert.ok(!document.getElementById("detail").textContent.includes("1000003"));
  globalThis.fetch = async () => ({ status: 401, ok: false });
  input.value = "incorrect-disposable-token-1234567890";
  document.getElementById("connect").onsubmit({ preventDefault() {} });
  await flush();
  await flush();
  assert.equal(document.getElementById("workspace").hidden, true);
  assert.equal(input.value, "");
  assert.match(
    document.getElementById("connection-status").textContent,
    /Disconnected/,
  );
});
