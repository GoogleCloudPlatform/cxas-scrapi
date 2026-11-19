# Vector DB Architecture, Embedding Model, Algorithm & Distance Guide

Use this reference to **proactively advise users** toward the highest-accuracy, lowest-latency, and most cost-effective Vertex AI Embedding and Vector Search configuration before writing `vector_steering.yaml` or provisioning cloud infrastructure.

---

## 1. Embedding Model Selection Matrix

| Embedding Model | Native Dims (MRL) | Languages | Latency | Best Use Case & When to Recommend |
| :--- | :--- | :--- | :--- | :--- |
| **`text-embedding-005`** *(Recommended English Default)* | `768` (`256`, `512`, `768`) | English + Code/IDs | `~25–35ms` | **Best choice for new English-only voice/chat agents.** Stronger separation on domain acronyms (`EOB`, `PA`, `ACH`), alphanumeric codes, and terse utterances. |
| **`text-embedding-004`** *(Stable Baseline)* | `768` (`256`, `512`, `768`) | English | `~25–35ms` | **Best when matching existing `004`-calibrated thresholds** (`0.75` / `0.08` delta) or pinned environments. |
| **`text-multilingual-embedding-002`** *(Multilingual / Bilingual)* | `768` (`256`, `512`, `768`) | **100+ languages** | `~30–40ms` | **Mandatory whenever `app.json` declares non-English or multilingual `supportedLanguageCodes`.** Maps all languages into a shared cross-lingual space. |
| **`gemini-embedding-001`** *(Multi-Clause Triage)* | `3072` (`768`, `1536`, `3072`) | **100+ languages** | `~50–90ms` | **Recommend for complex, multi-sentence chat/email/clinical triage** (truncate to `768` or `1536` dims with `normalize_l2: true`). |

### Critical Embedding Invariants
1. **Symmetric Task Type (`SEMANTIC_SIMILARITY`)**:
   - **Never** use asymmetric `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` for intent classification. Asymmetric retrieval warps short-phrase-to-short-phrase geometry and drops top-1 intent accuracy by `8–15%`.
   - Lock `task_type: "SEMANTIC_SIMILARITY"` identically on both offline indexing (`embed_and_sync.py`) and live queries (`resources/cloud_function/main.py`).
2. **Dimensionality & Matryoshka (MRL) Normalization**:
   - Keep `dimensions: 768` for corpora under `500,000` phrases (`~3 KB` per vector).
   - Always keep `normalize_l2: true` so truncated MRL vectors and `DOT_PRODUCT_DISTANCE` remain strictly unit-length (`||v|| = 1`).

---

## 2. Index Algorithm Selection: `BRUTE_FORCE` vs. `TREE_AH`

| Corpus Vector Count | Recommended `index_algorithm` | Recall | Query Latency | Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **`< 30,000` vectors** *(95% of intent routers)* | **`BRUTE_FORCE`** | **100.0% Exact** | `~5–15ms` | At `<30k` vectors (`<100 MB`), SIMD-vectorized exact scan matches ANN speed while guaranteeing **zero quantization or leaf-boundary recall misses**. |
| **`30,000 – 100M+` vectors** | **`TREE_AH`** | `~97–99%` Approximate | `~10–35ms` | Sub-linear scaling via two-level vector quantization and asymmetric hashing once exact scan exceeds `25ms`. |

### `TREE_AH` Small-Corpus Pitfall
If a team chooses `TREE_AH` on `< 20,000` vectors for future growth, **never combine `leaf_node_embedding_count: 1000` with `leaf_nodes_to_search_percent: 10`**—a `1,500`-phrase corpus creates only `2` leaf partitions and searches `1`, missing boundary intents. Instead use `leaf_node_embedding_count: 500` and `leaf_nodes_to_search_percent: 15–20`.

---

## 3. Distance Measure Selection

| `distance_measure_type` | Score Conversion | When to Recommend | Requirement |
| :--- | :--- | :--- | :--- |
| **`COSINE_DISTANCE`** *(Safe Default)* | `similarity = 1 - dist` | **Default for dev and general deployments.** Invariant to vector magnitude. | None. |
| **`DOT_PRODUCT_DISTANCE`** *(High-QPS SIMD)* | `similarity = dot(u, v)` | **Recommend for `>500 RPS` production `TREE_AH` clusters.** Unlocks `~15–25%` faster SIMD throughput on unit vectors. | **Requires `normalize_l2: true`** at both index and query time. |
| **`SQUARED_L2_DISTANCE`** | `similarity = 1 - dist / 2` | Legacy Euclidean indexes only. | Requires `normalize_l2: true`. |

---

## 4. Index Update Mode: `BATCH_UPDATE` vs. `STREAM_UPDATE`

- **`BATCH_UPDATE` (Default)**: `10–25 minute` zero-downtime blue/green index swap. Ideal for GitOps CI/CD workflows (`--upload-and-trigger`) with clean GCS JSONL audit snapshots.
- **`STREAM_UPDATE`**: `< 5 second` live vector upserts. Recommend when operations teams inject real-time outage or weather-waiver intents without waiting for a batch rebuild.
