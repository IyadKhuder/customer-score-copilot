"""Explicit offline boundary around the UNMODIFIED original agent and gates.

Only provider and score/retrieval backends are fixtures. Numerical guardrail,
policy engine, schema checks, composers and publication gate remain real.
"""

import ast
import copy
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import importlib
import json
from pathlib import Path
import socket
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def offline_application(source_root):
    root = Path(source_root).resolve()
    required = ["agent.py", "retriever.py", "policy_decisions.py", "guardrail.py"]
    if not all((root/"src"/name).is_file() for name in required):
        raise ValueError("source root must contain the existing application modules")
    tree = ast.parse((root/"src/retriever.py").read_text(encoding="utf-8"))
    schema = next(ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "RETRIEVER_TOOL_SCHEMA"
                          for target in node.targets))
    adapter = ModuleType("src.retriever")
    adapter.RETRIEVER_TOOL_SCHEMA = schema
    def forbid(*args, **kwargs):
        raise RuntimeError("Uncontrolled network/database/retrieval access is disabled in fixture replay")
    adapter.retrieve_policy = forbid
    # Avoid importing TF-IDF/dependency configuration for a fixed-retrieval test.
    # The schema is the actual source literal; the callable must be replaced by
    # the per-case isolated dispatch. No fake policy or numeric validator exists.
    previous = {k: v for k, v in sys.modules.items() if k == "src" or k.startswith("src.")}
    previous_path = sys.path[:]
    previous_bytecode = sys.dont_write_bytecode
    for key in previous: sys.modules.pop(key)
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True
    try:
        with patch.dict(sys.modules, {"src.retriever": adapter}), \
                patch.object(socket, "socket", forbid), patch.object(sqlite3, "connect", forbid):
            agent = importlib.import_module("src.agent")
            decision = importlib.import_module("src.policy_decisions").evaluate_policy_decision
            documents, policy_hashes = {}, {}
            for policy_id in ("POL-002", "POL-004", "POL-005"):
                found = list((root/"policy_memos").glob(policy_id+"_*.md"))
                if len(found) != 1:
                    raise ValueError("missing/ambiguous baseline policy file")
                path = found[0]
                documents[policy_id] = {"source": path.name, "text": path.read_text(encoding="utf-8")}
                policy_hashes[path.name] = sha256(path)
            hashes = {
                Path(module.__file__).name: sha256(Path(module.__file__))
                for name, module in sys.modules.items()
                if name.startswith("src.") and getattr(module, "__file__", None)
                and Path(module.__file__).parent == root/"src"
            }
            hashes["retriever.py (schema source only)"] = sha256(root/"src/retriever.py")
            yield SimpleNamespace(agent=agent, decide=decision, documents=documents,
                                  source_sha256=dict(sorted(hashes.items())),
                                  policy_sha256=policy_hashes)
    finally:
        for key in list(sys.modules):
            if key == "src" or key.startswith("src."): sys.modules.pop(key)
        sys.modules.update(previous)
        sys.path[:] = previous_path
        sys.dont_write_bytecode = previous_bytecode


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)


class ScriptedProvider:
    def __init__(self, claims, account):
        self.claims, self.account = claims, account

    def chat(self, *, messages, tools):
        return Reply(tool_calls=[ToolCall("history", "get_history", {"account_id": self.account}),
                                 ToolCall("policy", "retrieve_policy", {"query": "fixture policy evidence"})])

    def continue_with_tool_results(self, *, assistant_response, tool_results, tools):
        return Reply(text=json.dumps(self.claims))


def execute_case(app, case, fixture, rows, claims, *, omit_policies=()):
    request = case["request"]
    docs = [copy.deepcopy(app.documents[p]) for p in fixture["policy_ids"] if p not in omit_policies]
    dispatch = {
        "get_history": lambda account_id: copy.deepcopy(rows) if account_id == request["account_id"] else [],
        "retrieve_policy": lambda **kwargs: copy.deepcopy(docs),
    }
    kwargs = {"expected_account_id": request["account_id"], "expected_period": request["as_of_period"],
              "expected_market": request["market"]}
    if request["claim_scope"] == "grace_decline_events":
        kwargs.update({k: request[k] for k in ("site_onboarded_period", "last_exception_review_period")})
    with patch.dict(app.agent.ALL_TOOL_DISPATCH, dispatch, clear=True):
        return app.agent.run_agent(case["question"], ScriptedProvider(claims, request["account_id"]),
            publication_mode=request["publication_mode"], claim_scope=request["claim_scope"],
            verbose=False, **kwargs)


def sanitize(response):
    """Never persist model drafts or blocked fallback prose as public answers."""
    required = {"status", "publishable", "next_action", "reason_codes", "answer", "tool_evidence"}
    if not isinstance(response, dict) or not required <= set(response):
        raise ValueError("invalid agent result")
    publish = response["publishable"]
    if type(publish) is not bool or (publish and (response["status"] != "passed"
            or response["next_action"] != "publish" or response["reason_codes"])):
        raise ValueError("inconsistent publication result")
    if not publish and response["next_action"] == "publish":
        raise ValueError("inconsistent blocked result")
    policies = sorted({Path(p["source"]).name.split("_")[0]
        for entry in response["tool_evidence"] if entry.get("tool_name") == "retrieve_policy"
        for p in entry.get("result", []) if isinstance(p, dict) and isinstance(p.get("source"), str)})
    return {"status": response["status"], "publishable": publish,
            "next_action": response["next_action"], "reason_codes": response["reason_codes"],
            "public_answer": response["answer"] if publish else "",
            "retrieved_policy_ids": policies}


def evaluate_observation(case, observed, policy_decision):
    expected = case["expected"]
    warning = policy_decision["warning_signal_status"]
    count = sum(e["criterion"] == "consecutive_declines" for e in policy_decision["events"])
    text = " ".join(observed["public_answer"].casefold().split())
    checks = {
        "warning_status": warning == expected["warning_signal_status"],
        "dated_decline_count": count == expected["consecutive_decline_event_count"],
        "publication": observed["publishable"] == expected["publishable"],
        "next_action": observed["next_action"] == expected["next_action"],
        "reason_codes": sorted(observed["reason_codes"]) == sorted(expected["reason_codes"]),
        "required_policies": set(expected["required_policy_ids"]) <= set(observed["retrieved_policy_ids"]),
        "public_phrases": all(" ".join(p.casefold().split()) in text for p in expected["public_phrases"]),
        "draft_excluded": "untrusted" not in text and "automatically approve credit" not in text,
        "blocked_answer_empty": observed["publishable"] or observed["public_answer"] == "",
    }
    return checks, {"warning_signal_status": warning, "consecutive_decline_event_count": count}
