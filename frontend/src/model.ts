export interface Row {
  period: string;
  score: number;
  bin: number;
  market: number;
}
export interface Case {
  case_id: string;
  revision: number;
  workflow_status: string;
  verification_status: string;
  analyst_decision: string;
  package_hash: string | null;
  snapshot_hash: string;
  account_ids: number[];
  as_of_period: string;
  created_at: string;
  updated_at: string;
  evidence?: Row[];
  policy_memos?: { source_file: string; text: string; sha256: string }[];
  limitations?: string[];
  reason_codes?: string[];
  provenance?: Record<string, unknown>;
  analysis?: {
    scope?: string;
    scan?: {
      scan_outcome: string | null;
      coverage_complete: boolean | null;
      missing_baseline_months: string[];
      reason_codes: string[];
      next_action: string;
      policy_decision: unknown;
      narrative: { status: string };
    };
  } | null;
}
export function monthIndex(period: string): number {
  return Number(period.slice(0, 4)) * 12 + Number(period.slice(5, 7)) - 1;
}
export function gaps(rows: Row[]): string[] {
  const months = new Set(rows.map((r) => monthIndex(r.period)));
  if (!months.size) return [];
  const a = Math.min(...months),
    b = Math.max(...months);
  const result: string[] = [];
  for (let m = a; m <= b; m++)
    if (!months.has(m))
      result.push(
        `${Math.floor(m / 12)}-${String((m % 12) + 1).padStart(2, "0")}`,
      );
  return result;
}
export function segments(rows: Row[]): Row[][] {
  const sorted = [...rows].sort((a, b) => a.period.localeCompare(b.period));
  const result: Row[][] = [];
  for (const row of sorted) {
    const last = result.at(-1);
    if (!last || monthIndex(row.period) - monthIndex(last.at(-1)!.period) !== 1)
      result.push([row]);
    else last.push(row);
  }
  return result;
}
export function text<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  value: unknown,
  cls = "",
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  el.textContent =
    value === null || value === undefined ? "Unavailable" : String(value);
  el.className = cls;
  return el;
}
