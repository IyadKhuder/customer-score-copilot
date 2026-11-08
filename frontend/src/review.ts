import { Case, text } from "./model.js";
export interface DecisionBody {
  expected_revision: number;
  package_hash: string;
  decision: "approved" | "rejected";
  reason: string;
}
export function mountReview(
  root: HTMLElement,
  c: Case,
  submit: (body: DecisionBody) => Promise<void>,
) {
  const panel = document.createElement("section");
  panel.setAttribute("aria-label", "Analyst review");
  panel.append(text("h3", "Analyst review"));
  root.append(panel);
  if (
    c.workflow_status !== "awaiting_review" ||
    c.verification_status !== "passed" ||
    c.analyst_decision !== "pending" ||
    !c.package_hash ||
    !/^[a-f0-9]{64}$/.test(c.package_hash)
  ) {
    panel.append(
      text(
        "p",
        c.workflow_status === "completed"
          ? `Review closed: ${c.analyst_decision}. The saved package remains available.`
          : c.workflow_status === "held" ||
              c.verification_status === "blocked" ||
              c.verification_status === "incomplete"
            ? "Review unavailable: this case is held or verification is incomplete/blocked. Inspect the reasons and limitations; analyst approval cannot override verification."
            : "Review unavailable in this state. Refresh the case after analysis; a passed package awaiting review is required.",
      ),
    );
    return;
  }
  panel.append(
    text("p", `Reviewing revision ${c.revision} · package ${c.package_hash}`),
    text(
      "p",
      "Acceptance applies only to this frozen package and authorizes no credit action.",
    ),
  );
  const form = document.createElement("form");
  const choiceLabel = text("label", "Decision");
  const choice = document.createElement("select");
  choice.required = true;
  choice.id = "review-choice";
  choiceLabel.htmlFor = choice.id;
  for (const [value, label] of [
    ["", "Select a decision"],
    ["approved", "Approve package"],
    ["rejected", "Reject package"],
  ]) {
    const option = text("option", label);
    option.value = value;
    choice.append(option);
  }
  const reasonLabel = text(
    "label",
    "Reason (required, maximum 1000 characters)",
  );
  const reason = document.createElement("textarea");
  reason.id = "review-reason";
  reasonLabel.htmlFor = reason.id;
  reason.required = true;
  reason.maxLength = 1000;
  reason.rows = 4;
  const button = text("button", "Record decision");
  button.type = "submit";
  const status = text(
    "p",
    "Review the frozen evidence above before recording your decision.",
  );
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  let locked = false;
  const valid = () => {
    button.disabled =
      locked ||
      !["approved", "rejected"].includes(choice.value) ||
      !reason.value.trim() ||
      reason.value.trim().length > 1000;
  };
  choice.onchange = valid;
  reason.oninput = valid;
  form.onsubmit = async (event) => {
    event.preventDefault();
    valid();
    if (button.disabled) return;
    locked = true;
    valid();
    choice.disabled = true;
    reason.disabled = true;
    status.textContent =
      "Recording decision… Do not submit again. Leaving or disconnecting does not undo a saved decision.";
    try {
      await submit({
        expected_revision: c.revision,
        package_hash: c.package_hash!,
        decision: choice.value as DecisionBody["decision"],
        reason: reason.value.trim(),
      });
    } catch (err) {
      // The server may have committed before a connection failed. Require a read,
      // rather than turning a transport failure into a second mutation attempt.
      status.textContent = `${err instanceof Error ? err.message : "Decision request failed."} Outcome may be unknown. Use Refresh case before any further decision; no automatic retry was made.`;
      status.className = "error";
    }
  };
  form.append(choiceLabel, choice, reasonLabel, reason, button, status);
  panel.append(form);
  valid();
}
