import { text } from "./model.js";
interface Stats { eligible: number; measured: number; missing: number; maximum_ms?: number | null; median_ms: number | null; p95_ms: number | null; }
export interface Operations {
  window_start: string; window_end: string; cases_total: number;
  current_counts: Record<string, number>; submissions_in_window: number;
  failure_rate: { numerator: number; denominator: number; value: number | null };
  reason_counts: Record<string, number>;
  lifecycle: Record<string, Stats>; lifecycle_scope: string;
  timings: { window_start: string; window_end: string; collection_started_at: string;
    retained_samples: number; evicted_since_start: number; oldest_retained_at: string | null;
    scope: string; groups: (Stats & { kind: string; label: string; status: string })[] };
  provider: { reason: string };
}
const ms = (n: number | null) => n === null ? "Unavailable" : `${n.toFixed(2)} ms`;
function table(root: HTMLElement, headings: string[], rows: unknown[][]) {
  const el = document.createElement("table");
  const head = document.createElement("tr");
  for (const h of headings) head.append(text("th", h));
  el.append(head);
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const v of row) tr.append(text("td", v));
    el.append(tr);
  }
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  wrap.append(el); root.append(wrap);
}
export function renderOperations(root: HTMLElement, data: Operations) {
  root.replaceChildren(text("p", `Snapshot: ${data.window_end}. Activity window: ${data.window_start} to ${data.window_end}.`));
  table(root, ["Current backlog / state (all dates)", "Cases"],
    ["queued", "running", "awaiting_review", "held", "failed", "completed"].map(k => [k, data.current_counts[k] ?? 0]));
  root.append(text("p", `API-owned cases: ${data.cases_total}. New cases in window: ${data.submissions_in_window}. Failed first processing outcomes: ${data.failure_rate.numerator}/${data.failure_rate.denominator} (${data.failure_rate.value === null ? "Unavailable" : (data.failure_rate.value * 100).toFixed(1) + "%"}).`));
  root.append(text("p", `Oldest awaiting-review age: ${ms(data.lifecycle.review_wait_open?.maximum_ms ?? null)}.`));
  root.append(text("h3", "Lifecycle intervals"), text("p", data.lifecycle_scope));
  const names: Record<string, string> = {queue_delay: "Queue delay", processing_elapsed: "Processing elapsed (wall clock)", end_to_end: "Creation to processing outcome", review_wait_closed: "Analyst waiting — decided in window", review_wait_open: "Analyst waiting — currently open"};
  table(root, ["Interval", "Eligible", "Measured", "Missing", "Median", "p95"],
    Object.entries(data.lifecycle).map(([k,v]) => [names[k] ?? k, v.eligible, v.measured, v.missing, ms(v.median_ms), ms(v.p95_ms)]));
  root.append(text("h3", "Hold / failure reasons in window"));
  table(root, ["Operational reason", "Cases"], Object.entries(data.reason_counts));
  root.append(text("h3", "Request and stage samples"), text("p", data.timings.scope));
  root.append(text("p", `Collection started: ${data.timings.collection_started_at}. Requested sample window: ${data.timings.window_start} to ${data.timings.window_end}. Oldest retained: ${data.timings.oldest_retained_at ?? "Unavailable"}. Retained samples: ${data.timings.retained_samples}; capacity evictions since start: ${data.timings.evicted_since_start}. Counts are retained observations, not full traffic totals.`));
  root.append(text("p", "Submission includes snapshot and persistence, or idempotent replay. Worker cycle includes zero or several cases, graph/store overhead and recovery. Decision includes lock waiting and persistence. These overlap and must not be added. Pure analyzer execution time is unavailable."));
  table(root, ["Kind", "Operation", "Outcome", "Samples", "Median", "p95"],
    data.timings.groups.map(v => [v.kind, v.label, v.status, v.measured, ms(v.median_ms), ms(v.p95_ms)]));
  root.append(text("h3", "Provider coverage"), text("p", `Offline · live notes disabled. Started calls, token usage, usage coverage and monetary cost: unavailable. ${data.provider.reason}`));
}
