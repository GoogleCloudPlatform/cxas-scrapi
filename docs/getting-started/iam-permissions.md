---
title: IAM Permissions
description: Detailed GCP IAM permissions required for all CXAS SCRAPI features.
---

# IAM Permissions Guide

To run CXAS SCRAPI features, evaluations, and exploration tools, the Google Cloud principal (user or service account) executing the code must have the appropriate Identity and Access Management (IAM) permissions.

While SCRAPI works out-of-the-box with standard roles like `roles/ces.admin` or `roles/ces.editor`, you may want to configure fine-grained permissions for least-privilege access, especially in production or CI/CD environments.

This guide details the specific permissions required across different GCP services.

---

## 1. Conversational Agents (ces.googleapis.com)

These permissions control access to CX Agent Studio (CXAS) resources. Most SCRAPI core modules (e.g., `Apps`, `Agents`, `Sessions`, `Evaluations`) interact with these APIs.

### Apps and Environment Management
Required for managing CXAS Apps (used in `Apps` client and CLI):
*   `ces.googleapis.com/apps.list`
*   `ces.googleapis.com/apps.get`
*   `ces.googleapis.com/apps.create`
*   `ces.googleapis.com/apps.update`
*   `ces.googleapis.com/apps.delete`
*   `ces.googleapis.com/apps.import`
*   `ces.googleapis.com/apps.export`
*   `ces.googleapis.com/apps.runEvaluation`

### Agent Node & Design Management
Required for querying and updating sub-agents and their configurations:
*   `ces.googleapis.com/agents.list`
*   `ces.googleapis.com/agents.get`
*   `ces.googleapis.com/agents.create`
*   `ces.googleapis.com/agents.update` (includes `ces.googleapis.com/agents.updateCallbacks` for registering callbacks)
*   `ces.googleapis.com/agents.delete`

### Deployments and Environment Promotion
Required for managing deployments and channels:
*   `ces.googleapis.com/deployments.list`
*   `ces.googleapis.com/deployments.get`
*   `ces.googleapis.com/deployments.create`
*   `ces.googleapis.com/deployments.update`
*   `ces.googleapis.com/deployments.delete`

### Interactive Testing & Audio Sessions
Required for running simulations and interactive sessions:
*   `ces.googleapis.com/sessions.runSession` (Standard text-based turns)
*   `ces.googleapis.com/sessions.bidiRunSession` (Bidirectional audio streaming)
*   `ces.googleapis.com/tools.execute` (Executing/mocking tools during sessions)

### Custom Tools and Integrations
Required for registering and managing APIs/tools:
*   `ces.googleapis.com/tools.list`
*   `ces.googleapis.com/tools.get`
*   `ces.googleapis.com/tools.create`
*   `ces.googleapis.com/tools.update`
*   `ces.googleapis.com/tools.delete`
*   `ces.googleapis.com/toolsets.list`
*   `ces.googleapis.com/toolsets.get`
*   `ces.googleapis.com/toolsets.create`
*   `ces.googleapis.com/toolsets.update`
*   `ces.googleapis.com/toolsets.delete`

### Safety Settings and Guardrails
Required for managing safety policies and guardrails:
*   `ces.googleapis.com/guardrails.list`
*   `ces.googleapis.com/guardrails.get`
*   `ces.googleapis.com/guardrails.create`
*   `ces.googleapis.com/guardrails.update`
*   `ces.googleapis.com/guardrails.delete`

### Versions and Changelogs
Required for tracking draft history and restoring versions:
*   `ces.googleapis.com/appVersions.list`
*   `ces.googleapis.com/appVersions.get`
*   `ces.googleapis.com/appVersions.create`
*   `ces.googleapis.com/appVersions.delete`
*   `ces.googleapis.com/appVersions.restore`
*   `ces.googleapis.com/changelogs.list`
*   `ces.googleapis.com/changelogs.get`

### Evaluations and Metrics
Required for running batch evaluations and managing test suites:
*   `ces.googleapis.com/evaluations.list`
*   `ces.googleapis.com/evaluations.get`
*   `ces.googleapis.com/evaluations.create`
*   `ces.googleapis.com/evaluations.update`
*   `ces.googleapis.com/evaluations.delete`
*   `ces.googleapis.com/evaluationResults.list`
*   `ces.googleapis.com/evaluationResults.get`
*   `ces.googleapis.com/evaluationRuns.get`
*   `ces.googleapis.com/evaluationRuns.delete`
*   `ces.googleapis.com/evaluationDatasets.list`
*   `ces.googleapis.com/evaluationDatasets.get`
*   `ces.googleapis.com/evaluationDatasets.create`
*   `ces.googleapis.com/evaluationDatasets.update`
*   `ces.googleapis.com/evaluationDatasets.delete`
*   `ces.googleapis.com/evaluationExpectations.list`
*   `ces.googleapis.com/evaluationExpectations.get`
*   `ces.googleapis.com/evaluationExpectations.create`
*   `ces.googleapis.com/evaluationExpectations.update`
*   `ces.googleapis.com/evaluationExpectations.delete`

---

## 2. Contact Center Insights (contactcenterinsights.googleapis.com)

Required if you are using the insights extraction module (`insights.py`) to query conversational records:
*   `contactcenterinsights.googleapis.com/conversations.list`
*   `contactcenterinsights.googleapis.com/conversations.get`

---

## 3. Cloud Storage (storage.googleapis.com)

Required for uploading evaluation results, downloading test suites, and saving session audio files (used by GCS utility modules and reporting):
*   `storage.buckets.get` (To verify bucket existence and metadata)
*   `storage.objects.create` (To upload audio reports and test outputs)
*   `storage.objects.get` (To download raw test configuration files)
*   `storage.objects.list` (To list conversational artifacts in a bucket)

---

## 4. Supplementary Permissions

### Service Usage (serviceusage.googleapis.com)
Used as a pre-flight sanity check to ensure target APIs are active:
*   `serviceusage.services.get`

### Resource Manager (cloudresourcemanager.googleapis.com)
Required to query lists of project scopes (e.g., when listing apps across projects):
*   `cloudresourcemanager.googleapis.com/projects.get`
*   `cloudresourcemanager.googleapis.com/projects.list`

---

## 5. Important Note on Text-to-Speech (TTS)

If you are using audio simulation features (e.g., in `sessions.py` to simulate customer voice), please note:

*   **No IAM Permission for Standard TTS**: Standard Text-to-Speech synthesis (the `SynthesizeSpeech` RPC) **does not use IAM permissions**. Access is typically handled via API keys or is open to any authenticated principal in the project if the API is enabled. There is no `texttospeech.speech.synthesize` IAM permission.
*   **Long Audio Synthesis**: If you migrate to or use Long Audio Synthesis, you will need the following IAM permission:
    *   `texttospeech.locations.longAudioSynthesize`

---

## Predefined Roles

Instead of creating a custom role with all the above permissions, you can use these predefined roles:

*   **CXAS Admin (`roles/ces.admin`)**: Full control over all CXAS resources.
*   **CXAS Editor (`roles/ces.editor`)**: Can modify most CXAS resources, suitable for development.
*   **CXAS Viewer (`roles/ces.viewer`)**: Read-only access, ideal for CI/CD evaluation runners.
*   **Storage Object Admin (`roles/storage.objectAdmin`)**: If using GCS integration for audio/reports.
