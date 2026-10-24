"""Optional live-model demo for Module 5.4.4.

Requires the existing provider configuration and an API key. Running this
script may make paid API requests; offline unit tests do not.
"""
from src.agent import run_agent
from src.llm_client import get_llm_client


if __name__ == "__main__":
    client = get_llm_client()
    result = run_agent(
        "For account 1000008, report its score and bin on 1991-12-01. "
        "Use get_history, make no causal assumptions, and give only statements "
        "you can support with the tool results.",
        llm=client,
        verbose=True,
        publication_mode="claims_first",
        expected_account_id=1000008,
        expected_period="1991-12-01",
    )

    # Never print the unverified JSON draft from result['verification'].
    print("\nANALYST RESPONSE:")
    print(result["answer"])
    print("\nVERIFICATION SUMMARY:")
    for key in ("completed", "status", "publishable", "next_action", "reason_codes",
                "tool_rounds"):
        print(f"  {key}: {result[key]}")
    if result["verification"]:
        print(f'  rendering: {result["verification"].get("rendering", {}).get("status", "not_run")}')
        for section in ("schema", "numeric", "policy", "narrative"):
            detail = result["verification"].get(section, {})
            print(f"  {section}: {detail.get('status')}")
