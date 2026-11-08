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

test("review controller posts exact package, refreshes statuses, handles conflict and isolates late decision after disconnect", async () => {
  const { document } = parseHTML(
    readFileSync(new URL("../index.html", import.meta.url), "utf8"),
  );
  globalThis.document = document;
  const pending = { ...sample, package_hash: "a".repeat(64) };
  let saved = pending,
    mode = "success",
    lateDecision,
    posts = [];
  globalThis.fetch = async (path, options) => {
    if (options.method === "POST") {
      posts.push({ path, options });
      if (mode === "conflict") return { status: 409, ok: false };
      if (mode === "late")
        return new Promise((resolve) => {
          lateDecision = () =>
            resolve({
              status: 200,
              ok: true,
              json: async () => ({
                ...pending,
                workflow_status: "completed",
                analyst_decision: "approved",
              }),
            });
        });
      saved = {
        ...pending,
        revision: 3,
        workflow_status: "completed",
        analyst_decision: "rejected",
      };
      return { status: 200, ok: true, json: async () => saved };
    }
    return {
      status: 200,
      ok: true,
      json: async () =>
        path.startsWith("/cases?")
          ? { items: [saved], has_more: false }
          : saved,
    };
  };
  await import("../dist/app.js?review-controller");
  const settle = async () => {
    await flush();
    await flush();
  };
  const connect = async () => {
    document.getElementById("token").value = "disposable-test-token";
    document.getElementById("connect").onsubmit({ preventDefault() {} });
    await settle();
  };
  const open = async () => {
    document.querySelector("#queue button").onclick();
    await settle();
  };
  const decide = async () => {
    document.querySelector('#review-choice option[value="rejected"]').selected =
      true;
    const reason = document.getElementById("review-reason");
    reason.value = "My reason";
    reason.oninput();
    return document
      .querySelector("#detail form")
      .onsubmit({ preventDefault() {} });
  };
  await connect();
  await open();
  await decide();
  await settle();
  assert.deepEqual(JSON.parse(posts[0].options.body), {
    expected_revision: 2,
    package_hash: pending.package_hash,
    decision: "rejected",
    reason: "My reason",
  });
  assert.equal(posts[0].path, "/cases/demo/decisions");
  assert.equal(posts[0].options.headers["Content-Type"], "application/json");
  assert.match(
    document.getElementById("detail").textContent,
    /Decision recorded/,
  );
  assert.match(document.getElementById("queue").textContent, /rejected/);
  assert.equal(document.querySelector("#detail form"), null);
  saved = pending;
  mode = "conflict";
  await open();
  await decide();
  assert.match(
    document.getElementById("detail").textContent,
    /Decision conflict/,
  );
  assert.equal(document.querySelector("#detail form button").disabled, true);
  mode = "late";
  document.getElementById("refresh-case").onclick();
  await settle();
  const request = decide();
  await flush();
  document.getElementById("disconnect").onclick();
  lateDecision();
  await request;
  await settle();
  assert.equal(document.getElementById("workspace").hidden, true);
  assert.doesNotMatch(
    document.getElementById("detail").textContent,
    /Decision recorded/,
  );
});
