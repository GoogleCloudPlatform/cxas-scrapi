# GCP Infrastructure Provisioning, IAM, & GitOps CI/CD

This guide covers Google Cloud sizing, least-privilege IAM roles, cross-project ADC quota handling, and automated GitHub Actions vector re-indexing.

---

## 1. Vertex AI Vector Search Sizing Guide

Configure `shard_size`, `machine_type`, `min_replicas`, and `max_replicas` per environment in `vector_steering/vector_steering.yaml`:

| Environment Tier | Corpus Size | `shard_size` | `machine_type` | Replicas | Profile |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Sandbox / Dev** | `< 50,000` phrases | `SHARD_SIZE_SMALL` | `e2-standard-2` | `1..1` | Lowest cost; `<25ms` lookup |
| **Staging / UAT** | `< 100,000` phrases | `SHARD_SIZE_SMALL` | `e2-standard-2` or `n1-standard-16` | `1..2` | Production parity testing |
| **High-Volume Prod** | `10,000 – 1M+` phrases | `SHARD_SIZE_MEDIUM` | `n1-standard-16` | `2..6` | High-availability HA + autoscaling |

---

## 2. Least-Privilege IAM Matrix

### A. Deployment / CI Service Account
- `roles/aiplatform.user` — Generate embeddings and create/update Vertex AI Indexes & Endpoints
- `roles/storage.objectUser` — Backup and upload `symmetric_embeddings.json` and training CSV snapshots
- `roles/cloudfunctions.developer` & `roles/run.admin` — Deploy Gen2 Cloud Function and bind invoker IAM policies
- `roles/iam.serviceAccountUser` — Attach runtime service account to the Cloud Function
- `roles/serviceusage.serviceUsageConsumer` — Required on `quota_project_id` for ADC API quota attribution

### B. Cloud Function Runtime Service Account
- `roles/aiplatform.user` — Call Vertex AI Embeddings and `find_neighbors` on the Index Endpoint

### C. CX Agent Studio (CES) Service Agent
Vector Steering deploys the Cloud Function with `--no-allow-unauthenticated` and configures the CES OpenAPI toolset with `serviceAgentIdTokenAuthConfig`. Grant the CES service agent permission to invoke the Cloud Run service:
```bash
gcloud run services add-iam-policy-binding vector-steering-classifier \
  --region=us-central1 \
  --project=<PROJECT_ID> \
  --member="serviceAccount:service-<AGENT_PROJECT_NUMBER>@gcp-sa-ces.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

---

## 3. Cross-Project ADC Quota Resolution

When non-production GCP projects restrict direct user ADC quota attribution (`PERMISSION_DENIED: Caller does not have required permission to use project <project> for quota`), set `quota_project_id` in `vector_steering/vector_steering.yaml` to a project where your identity holds `roles/serviceusage.serviceUsageConsumer`. Both `embed_and_sync.py` and `provision_infra.py` apply `quota_project_id` automatically.

---

## 4. CI/CD Fast-Path Re-Indexing (`--upload-and-trigger`)

Developers can generate and commit updated JSONL embeddings locally (`embed_and_sync.py --skip-upload`), allowing CI pipelines to deploy branch updates without re-invoking the embedding API:

```bash
uv run python .agents/skills/cxas-vector-steering/scripts/embed_and_sync.py \
  --config vector_steering/vector_steering.yaml \
  --env "${TARGET_ENV}" \
  --upload-and-trigger
```

Use [../resources/ci/github-actions.template.yml](../resources/ci/github-actions.template.yml) to combine `analyze_dataset.py --strict`, `cxas lint --validate-only`, and OIDC Workload Identity `--upload-and-trigger` syncs.
