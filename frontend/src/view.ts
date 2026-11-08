import { Case, Row, gaps, segments, monthIndex, text } from "./model.js";
function section(root: HTMLElement, title: string, value: unknown) {
  root.append(text("h3", title), text("p", value));
}
function chart(rows: Row[]): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 900 300");
  svg.setAttribute("role", "img");
  svg.setAttribute(
    "aria-label",
    "Frozen score trajectory; dots are observations; lines connect consecutive months only. Score scale 0 to 1000.",
  );
  const draw = (tag: string, attrs: Record<string, string>, label?: string) => {
    const e = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (label) e.textContent = label;
    svg.append(e);
    return e;
  };
  const months = rows.map((r) => monthIndex(r.period));
  const lo = Math.min(...months),
    hi = Math.max(...months);
  const x = (r: Row) =>
    65 + ((monthIndex(r.period) - lo) / Math.max(1, hi - lo)) * 790;
  const y = (r: Row) => 260 - (r.score / 1000) * 235;
  for (const n of [0, 250, 500, 750, 1000]) {
    const yy = 260 - (n / 1000) * 235;
    draw("line", {
      x1: "65",
      x2: "855",
      y1: String(yy),
      y2: String(yy),
      stroke: "#d8e4e7",
    });
    draw(
      "text",
      { x: "10", y: String(yy + 5), fill: "#526d77", "font-size": "14" },
      String(n),
    );
  }
  for (const group of segments(rows))
    if (group.length > 1)
      draw("polyline", {
        points: group.map((r) => `${x(r)},${y(r)}`).join(" "),
        fill: "none",
        stroke: "#14665f",
        "stroke-width": "3",
      });
  for (const r of rows) {
    const dot = draw("circle", {
      cx: String(x(r)),
      cy: String(y(r)),
      r: "4",
      fill: "#14665f",
    });
    const t = document.createElementNS(ns, "title");
    t.textContent = `${r.period}: ${r.score}`;
    dot.append(t);
  }
  const sorted = [...rows].sort((a, b) => a.period.localeCompare(b.period));
  draw(
    "text",
    { x: "65", y: "287", fill: "#526d77", "font-size": "14" },
    sorted[0].period.slice(0, 7),
  );
  draw(
    "text",
    {
      x: "855",
      y: "287",
      fill: "#526d77",
      "font-size": "14",
      "text-anchor": "end",
    },
    sorted.at(-1)!.period.slice(0, 7),
  );
  return svg;
}
export function renderCase(root: HTMLElement, c: Case) {
  root.replaceChildren(
    text(
      "h2",
      `Account ${c.account_ids.join(", ")} · ${c.as_of_period.slice(0, 7)}`,
    ),
  );
  const refresh = text("button", "Refresh selected case");
  refresh.id = "refresh-case";
  root.append(refresh);
  const dl = document.createElement("dl");
  dl.className = "meta";
  for (const [label, value] of [
    ["Case ID", c.case_id],
    ["Workflow", c.workflow_status],
    ["Verification", c.verification_status],
    ["Analyst decision", c.analyst_decision],
    ["Revision", c.revision],
    ["Created", c.created_at],
    ["Updated", c.updated_at],
  ]) {
    const d = document.createElement("div");
    d.append(text("dt", label), text("dd", value));
    dl.append(d);
  }
  root.append(dl);
  root.append(
    text(
      "p",
      "Verification “passed” means the offline deterministic package completed. It does not establish evidence completeness or analyst approval.",
      "warning",
    ),
  );
  const scan = c.analysis?.scan;
  section(
    root,
    "Analysis scope",
    c.analysis?.scope ?? "No saved analysis is available for this case.",
  );
  section(root, "Scan outcome", scan?.scan_outcome ?? "Unavailable");
  section(root, "Suggested next step", scan?.next_action ?? "Unavailable");
  section(
    root,
    "Reason codes",
    [...(c.reason_codes ?? []), ...(scan?.reason_codes ?? [])].join(", ") ||
      "None recorded",
  );
  root.append(text("h3", "Frozen score trajectory"));
  const rows = c.evidence ?? [];
  const chartRows = rows.filter(
    (r) => Number.isInteger(r.score) && r.score >= 0 && r.score <= 1000,
  );
  if (chartRows.length !== rows.length)
    root.append(
      text(
        "p",
        "Invalid score observations are omitted from the chart. Inspect captured observations and failure diagnostics.",
        "warning",
      ),
    );
  if (chartRows.length) root.append(chart(chartRows));
  else root.append(text("p", "No captured observations."));
  section(
    root,
    "Coverage",
    scan?.coverage_complete === true
      ? "Policy baseline complete"
      : scan?.coverage_complete === false
        ? "Policy baseline incomplete"
        : "Policy baseline coverage unavailable",
  );
  section(
    root,
    "Missing policy baseline months",
    scan?.missing_baseline_months?.join(", ") ||
      "None reported / unavailable; inspect coverage status.",
  );
  section(
    root,
    "Gaps within captured history",
    gaps(rows).join(", ") ||
      "None within the first and last captured observations. This does not establish baseline completeness.",
  );
  root.append(
    text(
      "p",
      "Chart lines stop at missing months. No scores are interpolated. Fixed chart scale: 0–1000 (API validation range).",
    ),
  );
  const observations = document.createElement("details");
  observations.append(
    text("summary", `Captured observations (${rows.length})`),
  );
  const table = document.createElement("table");
  const head = document.createElement("tr");
  for (const label of ["Period", "Score", "Bin", "Market"])
    head.append(text("th", label));
  table.append(head);
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const v of [row.period, row.score, row.bin, row.market])
      tr.append(text("td", v));
    table.append(tr);
  }
  observations.append(table);
  root.append(observations);
  root.append(
    text("h3", "Frozen policy evidence"),
    text(
      "p",
      "Captured source text and SHA-256 hashes. These memos document provenance; the deterministic evaluator applies the compiled rules. They are not retrieved LLM citations.",
    ),
  );
  for (const memo of c.policy_memos ?? []) {
    const d = document.createElement("details");
    d.append(
      text("summary", memo.source_file),
      text("p", `SHA-256: ${memo.sha256}`),
      text("pre", memo.text),
    );
    root.append(d);
  }
  if (!c.policy_memos?.length)
    root.append(text("p", "No policy snapshot available."));
  root.append(text("h3", "Limitations"));
  for (const v of c.limitations ?? []) root.append(text("p", v));
  section(
    root,
    "LLM narrative",
    scan?.narrative?.status ?? "Not requested; live notes disabled",
  );
  const technical = document.createElement("details");
  technical.append(
    text("summary", "Package binding and deterministic result"),
    text(
      "pre",
      JSON.stringify(
        {
          snapshot_hash: c.snapshot_hash,
          package_hash: c.package_hash,
          provenance: c.provenance,
          policy_decision: scan?.policy_decision,
        },
        null,
        2,
      ),
    ),
  );
  root.append(technical);
}
