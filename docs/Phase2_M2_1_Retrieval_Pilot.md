# M2.1 retrieval development pilot

Run from the repository root. The baseline uses the existing project dependencies.
The optional dense run also uses onnxruntime and tokenizers.

```bash
python -m scripts.evaluate_retrieval_dev > tfidf.json
python -m scripts.evaluate_retrieval_dev --model-dir /path/to/model > dense.json
```

Use the public sentence-transformers/all-MiniLM-L6-v2 model at revision
`c9745ed1d9f207416be6d2e6f8de32d1f16199bf`. Place tokenizer.json and
onnx/model.onnx (as model.onnx) in the model directory.

| File | SHA-256 |
|---|---|
| model.onnx | 6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452 |
| tokenizer.json | be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037 |

Run environment was Python 3.12 with numpy 2.3.5, scikit-learn 1.8.0,
NLTK 3.10.4, onnxruntime 1.31.1 and tokenizers 0.23.3. The encoder caps
inputs at 256 tokens, mean-pools non-padding tokens and normalizes the
vectors so their dot product is cosine similarity.

| Method | Recall@3 | MRR@3 | Median query time |
|---|---:|---:|---:|
| Stemmed TF-IDF | 83.3% | 0.750 | 0.64 ms |
| MiniLM cosine | 100% | 1.000 | 7.66 ms |

Single CPU run on 6 November 2026 with six answerable development questions.
Index build took roughly 6 and 11 ms for TF-IDF and 366 and 1,263 ms for
MiniLM, for v1.0 and v1.1 respectively. MiniLM build times exclude model
load and download. Both methods returned candidates for the two unsupported
questions. The pilot measures ranking only. Held-out retrieval and
downstream abstention remain open.

## Expanded comparison

The v2 fixtures contain 16 development and 16 held-out questions. Each
has 14 answerable questions and two unsupported questions. The runner
checks each policy corpus against the frozen passage manifest.

Hybrid reciprocal rank fusion (RRF) adds 1 / (60 + rank) from each
retriever, with equal weights and 1-based ranks. Each retriever supplies
its full available ranking within the selected policy version. TF-IDF
omits zero-overlap passages as usual. Ties in fusion use the passage key
in ascending order. All three methods report the top three passages.
Unsupported questions are excluded from Recall@3 and MRR@3.

### Windows reproduction

From the repository root with the project virtual environment active:

```powershell
python -m pip install onnxruntime==1.31.1 tokenizers==0.23.3
$modelDir = Join-Path $env:TEMP "copilot-m21-minilm"
New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
$base = "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
Invoke-WebRequest "$base/tokenizer.json" -OutFile "$modelDir/tokenizer.json"
Invoke-WebRequest "$base/onnx/model.onnx" -OutFile "$modelDir/model.onnx"
Get-FileHash "$modelDir/tokenizer.json", "$modelDir/model.onnx"
```

Compare the hashes to the table above. Then run:

```powershell
foreach ($method in @("stemmed_tfidf", "dense_cosine", "hybrid_rrf")) {
    $output = python -m scripts.evaluate_retrieval_dev --split heldout --method $method --model-dir $modelDir
    if ($LASTEXITCODE -ne 0) { throw "Evaluation failed for $method" }
    $output | ConvertFrom-Json |
        Select-Object method, split, k, recall_at_k, mrr_at_k, median_query_ms
}
```

Use `--split dev` for the development comparison. Timing varies by machine.

### Results on the frozen fixtures

| Split | Method | Recall@3 | MRR@3 | Median query time |
|---|---|---:|---:|---:|
| Development | TF-IDF | 92.9% | 0.857 | 1.06 ms |
| Development | MiniLM | 100% | 0.917 | 12.00 ms |
| Development | Hybrid RRF | 100% | 0.881 | 8.56 ms |
| Held-out | TF-IDF | 100% | 0.893 | 1.17 ms |
| Held-out | MiniLM | 100% | 0.845 | 9.07 ms |
| Held-out | Hybrid RRF | 100% | 0.929 | 11.41 ms |

Hybrid ranked the first relevant passage first on 12 of 14 held-out
answerable questions, compared with 11 for TF-IDF and 10 for MiniLM.
Its MRR gain over TF-IDF came from moving Q29 from second to first.
All methods retrieved all labeled held-out evidence in the top three.
MiniLM had the highest development MRR. These small samples do not
establish a general ranking advantage.

Full rankings, fixture hashes, corpus manifests, index-build times and
query timings are under `docs/evaluation_results/m2_1`. These are single-run
CPU timings. Both unsupported held-out questions returned candidates
under all three methods. Answer verification and abstention were
outside this ranking comparison.
