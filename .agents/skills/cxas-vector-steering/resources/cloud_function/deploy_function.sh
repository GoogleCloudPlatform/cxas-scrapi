#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

set -euo pipefail

: "${PROJECT_ID:?ERROR: PROJECT_ID environment variable must be set.}"
: "${INDEX_ENDPOINT_ID:?ERROR: INDEX_ENDPOINT_ID environment variable must be set.}"

REGION="${REGION:-us-central1}"
FUNCTION_NAME="${FUNCTION_NAME:-vector-steering-classifier}"
DEPLOYED_INDEX_ID="${DEPLOYED_INDEX_ID:-vector_steering_index_deployed}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-text-embedding-005}"
EMBEDDING_TASK_TYPE="${EMBEDDING_TASK_TYPE:-SEMANTIC_SIMILARITY}"
OUTPUT_DIMENSIONALITY="${OUTPUT_DIMENSIONALITY:-768}"
NORMALIZE_L2="${NORMALIZE_L2:-true}"
DISTANCE_MEASURE_TYPE="${DISTANCE_MEASURE_TYPE:-COSINE_DISTANCE}"
SIMILARITY_THRESHOLD="${SIMILARITY_THRESHOLD:-0.75}"
DISAMBIGUATION_DELTA="${DISAMBIGUATION_DELTA:-0.08}"
SECONDARY_MATCH_FLOOR_OFFSET="${SECONDARY_MATCH_FLOOR_OFFSET:-0.15}"
NUM_NEIGHBORS="${NUM_NEIGHBORS:-5}"
MAX_TOP_MATCHES="${MAX_TOP_MATCHES:-3}"
INGRESS_SETTINGS="${INGRESS_SETTINGS:-all}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ENV_VARS="PROJECT_ID=${PROJECT_ID},LOCATION=${REGION},INDEX_ENDPOINT_ID=${INDEX_ENDPOINT_ID},DEPLOYED_INDEX_ID=${DEPLOYED_INDEX_ID},EMBEDDING_MODEL=${EMBEDDING_MODEL},EMBEDDING_TASK_TYPE=${EMBEDDING_TASK_TYPE},OUTPUT_DIMENSIONALITY=${OUTPUT_DIMENSIONALITY},NORMALIZE_L2=${NORMALIZE_L2},DISTANCE_MEASURE_TYPE=${DISTANCE_MEASURE_TYPE},SIMILARITY_THRESHOLD=${SIMILARITY_THRESHOLD},DISAMBIGUATION_DELTA=${DISAMBIGUATION_DELTA},SECONDARY_MATCH_FLOOR_OFFSET=${SECONDARY_MATCH_FLOOR_OFFSET},NUM_NEIGHBORS=${NUM_NEIGHBORS},MAX_TOP_MATCHES=${MAX_TOP_MATCHES}"

DEPLOY_ARGS=(
  gcloud functions deploy "${FUNCTION_NAME}"
  --gen2
  --runtime=python311
  --trigger-http
  --source="${SCRIPT_DIR}"
  --entry-point=classify_intent
  --region="${REGION}"
  --project="${PROJECT_ID}"
  --set-env-vars="${ENV_VARS}"
  --ingress-settings="${INGRESS_SETTINGS}"
  --no-allow-unauthenticated
)

if [[ -n "${SERVICE_ACCOUNT:-}" ]]; then
  DEPLOY_ARGS+=(--service-account="${SERVICE_ACCOUNT}")
fi

echo "Deploying Vector Steering Cloud Function '${FUNCTION_NAME}' to ${PROJECT_ID} (${REGION})..."
"${DEPLOY_ARGS[@]}"

if [[ -n "${AGENT_INVOKER_SA:-}" ]]; then
  echo "Granting roles/run.invoker to agent identity: ${AGENT_INVOKER_SA}..."
  gcloud run services add-iam-policy-binding "${FUNCTION_NAME}" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --member="serviceAccount:${AGENT_INVOKER_SA}" \
    --role="roles/run.invoker"
fi

FUNCTION_URL="$(gcloud functions describe "${FUNCTION_NAME}" --gen2 --region="${REGION}" --project="${PROJECT_ID}" --format='value(serviceConfig.uri)')"
echo "Vector Steering Cloud Function deployed successfully: ${FUNCTION_URL}"
