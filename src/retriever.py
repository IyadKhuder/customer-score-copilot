"""
retriever.py
The RAG side of the copilot: chunks the policy memos and retrieves the
passages most relevant to a query.

Here, we'll add PorterStemmer
Add stemming to the tokenizer

Concept: A stemmer strips words down to a common root by rule (not a dictionary),
so drop, dropped, dropping all collapse to the same token before TF-IDF ever sees them.
We'll plug it in as a custom tokenizer — a function we hand to TfidfVectorizer that
replaces its default "split into words" step with "split into words, then stem each one."

"""

import glob
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS as ENGLISH_STOP_WORDS_SET
from sklearn.metrics.pairwise import cosine_similarity
import re
from nltk.stem import PorterStemmer
import numpy as np, math

_stemmer = PorterStemmer()
_token_pattern = re.compile(r"(?u)\b\w\w+\b")


def stemming_tokenizer(text: str) -> list[str]:
    """Splits text into words and reduces each to its stem
    (e.g. 'dropped', 'dropping' -> 'drop'), so different grammatical
    forms of the same word are treated as identical tokens."""
    tokens = _token_pattern.findall(text.lower())
    return [_stemmer.stem(t) for t in tokens if t not in ENGLISH_STOP_WORDS_SET]


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
    vectorizer = TfidfVectorizer(tokenizer=stemming_tokenizer, token_pattern=None)

    matrix = vectorizer.fit_transform(corpus)
    return vectorizer, matrix


# Known limitation: Porter stemming reduces most word-form mismatches
# (e.g. drop/dropped/dropping -> drop), but its suffix rules aren't fully
# consistent -- "recovery" and "recovering" stem to different roots
# ("recoveri" vs "recov"), so a query using one form may miss a memo
# using the other. A smarter fix (lemmatization, or swapping TF-IDF for
# semantic embeddings, as noted in the blueprint) would resolve this,
# but is out of scope for this module.
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


# ---------------------------------------------------------------------------
# Module-level index, built once and reused by the tool function below
# ---------------------------------------------------------------------------

_chunks = None
_vectorizer = None
_matrix = None


def _ensure_index_built() -> None:
    """Builds the chunk index the first time it's needed, then reuses it."""
    global _chunks, _vectorizer, _matrix
    if _chunks is None:
        _chunks = load_all_chunks()
        _vectorizer, _matrix = build_index(_chunks)


def retrieve_policy(query: str, k: int = 3) -> list[dict]:
    """Tool function: retrieve the top-k relevant policy passages for a query."""
    _ensure_index_built()
    return retrieve(query, _chunks, _vectorizer, _matrix, k=k)


# ---------------------------------------------------------------------------
# Tool schema for the Anthropic Messages API (tool-use / function calling)
# ---------------------------------------------------------------------------

RETRIEVER_TOOL_SCHEMA = {
    "name": "retrieve_policy",
    "description": (
        "Search the credit-risk policy knowledge base (bin definitions, "
        "early-warning thresholds, recommended actions by risk band, "
        "market-level exceptions, data-quality flags, recovery rules, "
        "escalation triggers) for passages relevant to a query. Use this "
        "to ground any explanation or recommendation in actual policy text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for, e.g. 'two bin downgrade action'"},
            "k": {"type": "integer", "description": "How many passages to return (default 3)"},
        },
        "required": ["query"],
    },
}



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


    ## The vectorizer:
    # It has 2 main structures: vectorizer.idf_ and vectorizer.vocabulary_:
    """
    The two structures are linked by the column number:
    python
    ```
    col = vectorizer.vocabulary_['002']   # -> 1
    vectorizer.idf_[col]                  # -> 1.693
    ```
    So '001' → col 0 → IDF 2.386, '002' → col 1 → IDF 1.693, '003' → col 2 → IDF 1.981, and so on.
    """
    df_counts = np.asarray((matrix > 0).sum(axis=0)).ravel()  # chunks containing each word
    for w in ['001', '002', '003']:
        col = vectorizer.vocabulary_[w]
        print(w, col, "df =", df_counts[col], "idf =", round(vectorizer.idf_[col], 3),
              "by hand =", round(math.log((1 + 7) / (1 + df_counts[col])) + 1, 3))

    ## MATRIX
    """
    matrix is 7 × 180 = 1,260 cells, but only 291 are non-zero (about 23%). The screenshot confirms it: nnz = 291 and size = 291. A sparse matrix stores only those 291 values and treats everything else as zero.
    data and indices work as a pair
    The matrix is in CSR format (Compressed Sparse Row, as format = 'csr' shows), which stores three arrays:
    
    Array	Shape	Meaning
    data	(291,)	the non-zero TF-IDF values, row by row
    indices	(291,)	the column number of each value in data
    indptr	(8,)	where each row starts and ends inside those two arrays
    
    indptr is the piece that tells you which row each value belongs to. In your screenshot it is [0 44 83 134 178 222 252 291]. That means:
    
    row 0 (first chunk) occupies positions 0 to 43, so it has 44 non-zero words
    row 1 occupies positions 44 to 82, so it has 39
    and so on, up to the last row ending at 291
    
    The 8 entries are "number of rows + 1": 7 start positions and the final end.
    
    Decoding one cell:
    Take the first chunk. Its indices start 0, 15, 26, 27, 34, ... and its data start 0.093, 0.093, 0.077, 0.663, .... Pairing them up and looking each column number up in your vocabulary list gives:
    col 0, '001': 0.093
    col 15, 'adjac': 0.093
    col 26, 'behavior': 0.077
    col 27, 'bin': 0.663
    
    So the first chunk's strongest word is bin. That fits a chunk about score bin definitions, where "bin" is repeated many times. Within a row, the column numbers are sorted ascending.

    """
    print(matrix.nnz, matrix.indptr)  # 291 and the 8 boundaries

    names = vectorizer.get_feature_names_out()
    row = 0
    s, e = matrix.indptr[row], matrix.indptr[row + 1]
    for col, val in zip(matrix.indices[s:s + 5], matrix.data[s:s + 5]):
        print(col, names[col], round(val, 3))  # first 5 words of chunk 0

    # the same value fetched the normal way
    print(matrix[0, vectorizer.vocabulary_['bin']])


    print("\n--- Simulated tool_use dispatch ---")
    simulated_call = {"name": "retrieve_policy", "input": {"query": "customer dropped two bins", "k": 2}}
    result = retrieve_policy(**simulated_call["input"])
    print(f"Called {simulated_call['name']}({simulated_call['input']}) ->")
    for hit in result:
        print(f"  [{hit['relevance']}] {hit['source']} — {hit['heading']}")



"""
Output:

Query: 'customer dropped two bins in one month'
  [0.211] POL-001_score_bin_definitions.md — POL-001 — Score Bin Definitions
  [0.165] POL-002_early_warning_thresholds.md — POL-002 — Early-Warning Thresholds

Query: 'new market onboarded recently'
  [0.187] POL-004_site_market_exception_rules.md — POL-004 — Site/Market-Level Exception Rules
  [0.132] POL-003_recommended_actions_by_risk_band.md — POL-003 — Recommended Actions by Risk Band

Query: 'account recovering after a downgrade'
  [0.296] POL-007_escalation_and_committee_review_triggers.md — POL-007 — Escalation and Committee Review Triggers
  [0.274] POL-002_early_warning_thresholds.md — POL-002 — Early-Warning Thresholds

Process finished with exit code 0

Analysis of the above-obtained results:
--------------------------------------------
Queries 1 and 2 now retrieve exactly what we expected. Query 3 is still not what we'd want — POL-006 (recovery) doesn't even make the top 2. Here's the specific reason, and it's a different failure mode than before:

recovering -> recov       (Porter stem)
recovery   -> recoveri    (Porter stem)

The Porter algorithm's suffix rules treat -ing and -y endings differently, 
so it stems these two forms of the same word to two different roots. 
Stemming isn't a perfect solution — it's a fixed set of suffix-stripping rules, 
not real linguistic knowledge, and this is a known, 
well-documented quirk of Porter specifically. 
downgrade also still doesn't match downward 
(different roots even after stemming — 
they're actually different words, not just different forms of one), 
so POL-006's best signal never fires, 
and the query falls back on the generic word account, 
which favors POL-007 (uses it 4×) over POL-006 (uses it once).

"""

