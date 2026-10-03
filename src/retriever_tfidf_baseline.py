"""
retriever.py
The RAG side of the copilot: chunks the policy memos and retrieves the
passages most relevant to a query.
"""

import glob
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

POLICY_DIR = "policy_memos"


def _chunk_markdown(text: str, source: str) -> list[dict]:
    """
    Splits one markdown memo into paragraph-level chunks, keeping the
    nearest '#' heading as context for each chunk.
    """
    lines = text.split("\n")
    chunks = []
    current_heading = ""
    buffer = []

    def flush():
        content = "\n".join(buffer).strip()
        if content:
            chunks.append({
                "source": source,
                "heading": current_heading,
                "text": content,
            })
        buffer.clear()

    for line in lines:
        if line.startswith("#"):
            flush()
            current_heading = line.lstrip("#").strip()
        elif line.strip() == "":
            flush()
        else:
            buffer.append(line)
    flush()
    return chunks


def load_all_chunks(policy_dir: str = POLICY_DIR) -> list[dict]:
    """Reads every .md file in policy_dir and returns all chunks combined."""
    chunks = []
    for path in sorted(glob.glob(f"{policy_dir}/*.md")):
        text = Path(path).read_text(encoding="utf-8")
        chunks.extend(_chunk_markdown(text, source=Path(path).name))
    return chunks


def build_index(chunks: list[dict]):
    """
    Fits a TF-IDF vectorizer on all chunks (heading + text combined) and
    returns (vectorizer, matrix) — the vectorizer knows the vocabulary,
    the matrix holds one row per chunk.
    """
    corpus = [f"{c['heading']} {c['text']}" for c in chunks]
    vectorizer = TfidfVectorizer(stop_words="english")
    matrix = vectorizer.fit_transform(corpus)
    return vectorizer, matrix


def retrieve(query: str, chunks: list[dict], vectorizer, matrix, k: int = 3) -> list[dict]:
    """
    Returns the top-k chunks most relevant to the query, each with a
    relevance score, ranked highest first. Chunks with zero relevance
    (no vocabulary overlap with the query) are excluded.
    """
    query_vector = vectorizer.transform([query])
    scores = cosine_similarity(query_vector, matrix)[0]

    ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

    results = []
    for i in ranked_indices[:k]:
        if scores[i] <= 0:
            continue
        chunk = chunks[i]
        results.append({
            "source": chunk["source"],
            "heading": chunk["heading"],
            "text": chunk["text"],
            "relevance": round(float(scores[i]), 3),
        })
    return results


if __name__ == "__main__":
    chunks = load_all_chunks()
    print(f"Loaded {len(chunks)} chunks from "
          f"{len(set(c['source'] for c in chunks))} documents\n")

    vectorizer, matrix = build_index(chunks)
    print(f"Vocabulary size: {len(vectorizer.vocabulary_)}")
    print(f"Matrix shape: {matrix.shape}  (chunks x vocabulary words)")

    # Peek at a few vocabulary words, to see what survived stop-word removal
    sample_words = list(vectorizer.vocabulary_.keys())[:10]
    print(f"Sample vocabulary words: {sample_words}")

    print("\n--- Test queries ---")
    test_queries = [
        "customer dropped two bins in one month",
        "new market onboarded recently",
        "account recovering after a downgrade",
    ]
    for query in test_queries:
        print(f"\nQuery: {query!r}")
        for hit in retrieve(query, chunks, vectorizer, matrix, k=2):
            print(f"  [{hit['relevance']}] {hit['source']} — {hit['heading']}")


"""
Output:

--- Test queries ---

Query: 'customer dropped two bins in one month'
  [0.173] POL-003_recommended_actions_by_risk_band.md — POL-003 — Recommended Actions by Risk Band
  [0.121] POL-001_score_bin_definitions.md — POL-001 — Score Bin Definitions

Query: 'new market onboarded recently'
  [0.218] POL-004_site_market_exception_rules.md — POL-004 — Site/Market-Level Exception Rules
  [0.075] POL-003_recommended_actions_by_risk_band.md — POL-003 — Recommended Actions by Risk Band

Query: 'account recovering after a downgrade'
  [0.287] POL-007_escalation_and_committee_review_triggers.md — POL-007 — Escalation and Committee Review Triggers
  [0.095] POL-006_score_recovery_and_reinstatement.md — POL-006 — Score Recovery and Reinstatement

Process finished with exit code 0



Word-form mismatches count as completely different words:

Query used	Memos actually use	Match?
dropped	drop (POL-002, POL-004, POL-005)	✗ no match
downgrade	downward (POL-001, POL-002, POL-007)	✗ no match
customer	account (used everywhere)	✗ no match
recovering	recovery (POL-005, POL-006)	✗ no match

Every one of your "expected" memos got locked out of the top match specifically because the


"""

