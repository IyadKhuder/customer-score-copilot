import { Case, text } from "./model.js";
import { mountReview } from "./review.js";
import { renderCase } from "./view.js";
const el = <T extends HTMLElement>(id: string) =>
  document.getElementById(id) as T;
let token = "",
  offset = 0,
  epoch = 0,
  selected = "";
let queueRequest: AbortController | undefined,
  detailRequest: AbortController | undefined;
const size = 20;
function disconnect() {
  token = "";
  epoch++;
  queueRequest?.abort();
  detailRequest?.abort();
  selected = "";
  offset = 0;
  el("queue").replaceChildren();
  el("detail").replaceChildren(
    text("h2", "Evidence view"),
    text("p", "Select a case to inspect its saved package."),
  );
  el("workspace").hidden = true;
  el("disconnect").hidden = true;
  el("connect").hidden = false;
  el<HTMLInputElement>("token").value = "";
  el("connection-status").textContent =
    "Disconnected. Enter your local API token to connect.";
}
async function read(path: string, signal: AbortSignal, body?: unknown) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    signal,
  });
  if (response.status === 401) {
    disconnect();
    throw new Error(
      "Authentication failed. Enter the configured local API token.",
    );
  }
  if (response.status === 409)
    throw new Error(
      "Decision conflict or stale package. Reload the case and review the saved status before deciding again.",
    );
  if (!response.ok)
    throw new Error(
      response.status === 404
        ? "Case no longer available."
        : `API unavailable (HTTP ${response.status}). Retry when the service is ready.`,
    );
  return response.json();
}
function error(err: unknown) {
  return err instanceof Error ? err.message : "Request unavailable. Retry.";
}
async function detail(id: string) {
  selected = id;
  detailRequest?.abort();
  const controller = new AbortController();
  detailRequest = controller;
  const generation = epoch;
  el("detail").replaceChildren(text("p", "Loading saved evidence…"));
  try {
    const c = (await read(
      `/cases/${encodeURIComponent(id)}`,
      controller.signal,
    )) as Case;
    if (generation !== epoch || selected !== id || controller.signal.aborted)
      return;
    renderCase(el("detail"), c);
    mountReview(el("detail"), c, async (body) => {
      // POST is never retried. A late result cannot restore a previous case/session.
      const saved = (await read(
        `/cases/${encodeURIComponent(id)}/decisions`,
        controller.signal,
        body,
      )) as Case;
      if (generation !== epoch || selected !== id || controller.signal.aborted)
        return;
      renderCase(el("detail"), saved);
      mountReview(el("detail"), saved, async () => {});
      el("detail").append(
        text("p", "Decision recorded. Saved status shown above.", "success"),
      );
      el("refresh-case").onclick = () => void detail(id);
      void queue();
    });
    el("refresh-case").onclick = () => void detail(id);
  } catch (e) {
    if (controller.signal.aborted || generation !== epoch) return;
    el("detail").replaceChildren(text("p", error(e), "error"));
    const retry = text("button", "Retry case");
    retry.onclick = () => void detail(id);
    el("detail").append(retry);
  }
}
async function queue() {
  queueRequest?.abort();
  const controller = new AbortController();
  queueRequest = controller;
  const generation = epoch;
  el("queue-status").textContent = "Loading cases…";
  el("queue").replaceChildren();
  el<HTMLButtonElement>("previous").disabled = true;
  el<HTMLButtonElement>("next").disabled = true;
  const params = new URLSearchParams({
    limit: String(size),
    offset: String(offset),
  });
  const status = el<HTMLSelectElement>("workflow").value;
  if (status) params.set("workflow_status", status);
  try {
    const data = (await read(`/cases?${params}`, controller.signal)) as {
      items: Case[];
      has_more: boolean;
    };
    if (generation !== epoch || controller.signal.aborted) return;
    el("connect").hidden = true;
    el("disconnect").hidden = false;
    el("workspace").hidden = false;
    el("connection-status").textContent =
      "Connected · analyst review workspace · token held in memory";
    el("queue-status").textContent = data.items.length
      ? "Newest submissions first. Status filter applies before pagination."
      : "No cases match this queue. Submit cases using the existing API smoke demonstration.";
    const table = document.createElement("table");
    const head = document.createElement("tr");
    for (const name of [
      "Account / assessment",
      "Workflow",
      "Verification",
      "Decision",
      "Open",
    ])
      head.append(text("th", name));
    table.append(head);
    for (const c of data.items) {
      const tr = document.createElement("tr");
      for (const v of [
        `${c.account_ids.join(", ")} / ${c.as_of_period.slice(0, 7)}`,
        c.workflow_status,
        c.verification_status,
        c.analyst_decision,
      ])
        tr.append(text("td", v));
      const td = document.createElement("td");
      const open = text("button", "View evidence");
      open.setAttribute("aria-label", `View evidence ${c.case_id}`);
      open.onclick = () => {
        void detail(c.case_id);
        el("detail").focus();
      };
      td.append(open);
      tr.append(td);
      table.append(tr);
    }
    el("queue").append(table);
    el("page").textContent = `Page ${offset / size + 1}`;
    el<HTMLButtonElement>("previous").disabled = offset === 0;
    el<HTMLButtonElement>("next").disabled =
      !data.has_more || offset + size > 100000;
  } catch (e) {
    if (controller.signal.aborted || generation !== epoch) return;
    el("queue-status").textContent = error(e);
    el("connection-status").textContent = error(e);
    el<HTMLButtonElement>("previous").disabled = offset === 0;
  }
}
el("connect").onsubmit = (e) => {
  e.preventDefault();
  token = el<HTMLInputElement>("token").value;
  el<HTMLInputElement>("token").value = "";
  offset = 0;
  void queue();
};
el("disconnect").onclick = disconnect;
el("refresh").onclick = () => void queue();
el("workflow").onchange = () => {
  offset = 0;
  void queue();
};
el("previous").onclick = () => {
  offset = Math.max(0, offset - size);
  void queue();
};
el("next").onclick = () => {
  offset += size;
  void queue();
};
