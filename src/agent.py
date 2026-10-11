"""Behavioral Score Trajectory Copilot — Module 4.7c agent loop.

Place this file at src/agent.py in the project.
The provider adapter keeps provider-specific conversation history.
"""

from src.score_tools import TOOL_DISPATCH, TOOL_SCHEMAS
from src.retriever import RETRIEVER_TOOL_SCHEMA, retrieve_policy


SYSTEM_PROMPT = """You are a behavioral credit-score analysis copilot.

Use score query tools to obtain account records and numeric facts; never invent
scores, dates, changes, or account details. Use retrieve_policy to obtain policy
rules and recommendations before interpreting risk or suggesting an action.
Do not guess policy thresholds from memory. Distinguish observed score movements
from their possible causes: score history alone does not prove why behavior changed.
If evidence is missing, say so. Note possible scoring/data-quality artifacts and
cite policy memo names where the retrieved passages support your conclusions.

Before giving a risk recommendation, evaluate the applicable
policy criteria against the available score-tool evidence.
Retrieve the relevant policy passages when needed.

Explicitly distinguish:
- Historical triggers that occurred during the account's history.
- Triggers applicable to the latest reporting period.
- Triggers within the policy's rolling look-back window.

For each relevant criterion, state whether it is met,
not met, or cannot be determined.

Do not defer an evaluation to manual review if the
available tool results and policy rules are sufficient
to perform it. If evidence is insufficient or a policy
rule is ambiguous, explain precisely what is missing
or ambiguous.


When evaluating time-dependent policy triggers, distinguish
between a historical trigger occurrence, a trigger that is
still ongoing in the latest reporting period, and the
current status of a previously triggered warning.

A score recovery may end an ongoing decline streak, but
it does not automatically erase an earlier warning event.

When using rolling look-back windows, examine the dates
of qualifying events and explain the window interpretation.

Do not assume that a logged warning automatically expires
unless the retrieved policy explicitly specifies a
clearance or expiration rule.

If the policy does not define how or when an existing
warning is cleared, identify that ambiguity rather than
asserting that the account has no active warning.

"""

# Project-owned schemas: the LLM adapter converts them to provider format.
ALL_TOOLS = [*TOOL_SCHEMAS, RETRIEVER_TOOL_SCHEMA]

# Only these named, trusted Python functions can be executed.
ALL_TOOL_DISPATCH = {**TOOL_DISPATCH, "retrieve_policy": retrieve_policy}


def run_agent(question: str, llm, max_tool_rounds: int = 8,
              verbose: bool = True) -> dict:
    """Run the tool loop; optionally print a human-readable execution trace."""
    if not question.strip():
        raise ValueError("The question cannot be empty.")
    if max_tool_rounds < 1:
        raise ValueError("max_tool_rounds must be at least 1.")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    response = llm.chat(messages=messages, tools=ALL_TOOLS)
    evidence = []
    rounds = 0

    while response.tool_calls:
        if rounds >= max_tool_rounds:
            if verbose:
                print(f"\nAgent stopped at the {max_tool_rounds}-round safety limit.")
            return {
                "answer": None,
                "completed": False,
                "tool_evidence": evidence,
                "tool_rounds": rounds,
            }

        if verbose:
            print(f"\n=== TOOL ROUND {rounds + 1} ===")

        results = []
        for call in response.tool_calls:
            if verbose:
                arguments = ", ".join(
                    f"{key}={value!r}" for key, value in call.arguments.items()
                )
                print(f"  - {call.name}({arguments})")

            function = ALL_TOOL_DISPATCH.get(call.name)
            if function is None:
                result = {"error": f"Unknown tool: {call.name}"}
            else:
                try:
                    result = function(**call.arguments)
                except (TypeError, ValueError) as exc:
                    result = {"error": f"Invalid tool arguments: {exc}"}

            results.append(result)
            evidence.append({
                "tool_call_id": call.id,
                "tool_name": call.name,
                "arguments": call.arguments,
                "result": result,
            })

            if verbose and isinstance(result, dict) and "error" in result:
                print(f"    ! {result['error']}")

        if verbose:
            print("  Sending tool results back to the model...")

        response = llm.continue_with_tool_results(
            assistant_response=response,
            tool_results=results,
            tools=ALL_TOOLS,
        )
        rounds += 1

    if verbose:
        print(f"\n=== AGENT FINISHED ===")
        print(f"Tool rounds: {rounds}")
        print(f"Total tool calls: {len(evidence)}")

    return {
        "answer": response.text,
        "completed": True,
        "tool_evidence": evidence,
        "tool_rounds": rounds,
    }
