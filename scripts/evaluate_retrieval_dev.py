"""Run from the repo root with python -m scripts.evaluate_retrieval_dev."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from src.retriever import POLICY_DIRS, build_index, load_all_chunks, retrieve

ROOT = Path(__file__).resolve().parents[1]
K = 3
RRF_OFFSET = 60


def passage_key(chunk):
    return chunk["source"], chunk["heading"], chunk["text"]


def fuse_rankings(*rankings):
    scores = Counter()
    for ranking in rankings:
        for rank, result in enumerate(ranking, 1):
            scores[passage_key(result)] += 1 / (RRF_OFFSET + rank)
    keys = sorted(scores, key=lambda key: (-scores[key], key))
    return [
        dict(source=source, heading=heading, text=body, relevance=scores[(source, heading, body)])
        for source, heading, body in keys[:K]
    ]


def evaluate_version(version, cases, method, embed, frozen_manifest):
    chunks = load_all_chunks(POLICY_DIRS[version])
    counts = Counter()
    passage_ids = {}
    manifest = {}
    for chunk in chunks:
        memo = chunk["source"].split("_", 1)[0]
        counts[memo] += 1
        passage_id = f"{version}/{memo}/p{counts[memo]:03d}"
        key = passage_key(chunk)
        if key in passage_ids:
            raise ValueError(f"Duplicate passage in {chunk['source']}")
        passage_ids[key] = passage_id
        manifest[passage_id] = {
            "source": chunk["source"],
            "heading": chunk["heading"],
            "sha256": hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest(),
        }

    if frozen_manifest is not None:
        expected_manifest = {
            key: value for key, value in frozen_manifest.items()
            if key.startswith(f"{version}/")
        }
        if manifest != expected_manifest:
            raise ValueError(f"Corpus differs from frozen manifest for {version}")

    start = perf_counter()
    if method != "dense_cosine":
        vectorizer, sparse_matrix = build_index(chunks)
    if method != "stemmed_tfidf":
        dense_matrix = embed([f"{chunk['heading']} {chunk['text']}" for chunk in chunks])
    build_ms = (perf_counter() - start) * 1000
    rows = []
    for case in cases:
        expected = set(case["expected"])
        if expected - manifest.keys():
            raise ValueError(f"Unknown expected passage in {case['id']}")
        start = perf_counter()
        depth = len(chunks) if method == "hybrid_rrf" else K
        if method != "dense_cosine":
            sparse = retrieve(case["question"], chunks, vectorizer, sparse_matrix, k=depth)
        if method != "stemmed_tfidf":
            scores = dense_matrix @ embed([case["question"]])[0]
            indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            dense = [{**chunks[i], "relevance": float(scores[i])} for i in indices[:depth]]
        if method == "hybrid_rrf":
            results = fuse_rankings(sparse, dense)
        elif method == "dense_cosine":
            results = dense
        else:
            results = sparse
        query_ms = (perf_counter() - start) * 1000
        ranked = [passage_ids[passage_key(result)] for result in results]
        ranks = [rank for rank, passage in enumerate(ranked, 1) if passage in expected]
        rows.append({
            "id": case["id"],
            "expected": case["expected"],
            "ranked": ranked,
            "scores": [result["relevance"] for result in results],
            "recall": len(ranks) / len(expected) if expected else None,
            "reciprocal_rank": (1 / ranks[0] if ranks else 0.0) if expected else None,
            "query_ms": query_ms,
        })
    return {"index_build_ms": build_ms, "manifest": manifest, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, help="Local MiniLM ONNX and tokenizer files")
    parser.add_argument("--split", choices=("pilot", "dev", "heldout"), default="pilot")
    parser.add_argument("--method", choices=("stemmed_tfidf", "dense_cosine", "hybrid_rrf"))
    args = parser.parse_args()
    method = args.method or ("dense_cosine" if args.model_dir else "stemmed_tfidf")
    if method != "stemmed_tfidf" and args.model_dir is None:
        parser.error("--model-dir is required for dense and hybrid retrieval")
    embed = None
    if method != "stemmed_tfidf":
        from scripts.minilm_onnx import load_encoder

        embed = load_encoder(args.model_dir)

    evaluation = ROOT / "evaluation"
    suffix = "dev_v1" if args.split == "pilot" else f"{args.split}_v2"
    fixture = evaluation / f"retrieval_reference_{suffix}.json"
    frozen_manifest = None
    if args.split != "pilot":
        frozen_manifest = json.loads(
            (evaluation / "retrieval_passage_manifest_v2.json").read_text(encoding="utf-8")
        )
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    versions = {
        version: evaluate_version(
            version, [case for case in cases if case["version"] == version],
            method, embed, frozen_manifest,
        )
        for version in sorted({case["version"] for case in cases})
    }
    answerable = [
        row for report in versions.values() for row in report["rows"]
        if row["expected"]
    ]
    print(json.dumps({
        "method": method,
        "split": args.split,
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "median_query_ms": median(
            row["query_ms"] for report in versions.values() for row in report["rows"]
        ),
        "k": K,
        "recall_at_k": mean(row["recall"] for row in answerable),
        "mrr_at_k": mean(row["reciprocal_rank"] for row in answerable),
        "versions": versions,
    }, indent=2))


if __name__ == "__main__":
    main()
