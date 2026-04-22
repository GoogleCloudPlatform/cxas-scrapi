# Onboarding Flow & Configuration

## Onboarding Flow (first-time users only)

When the readiness check identifies a first-time user (no `.venv/`):

1. **Create virtualenv and install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
   Then find `cxas-scrapi` source (look for `setup.py` containing `cxas-scrapi` in parent directories or siblings) and install:
   ```bash
   pip install -e <path_to_cxas_scrapi> --quiet
   ```
2. **Collect project details** -- see Configuration below.
3. Confirm with the user: "Your environment is set up. You're connected to **[app_name]** on **[project_id]**."
4. If the user's original request was "build me an agent" -> proceed to the build sub-skill. If they connected to an existing app -> ask: "Do you want to create evals for this existing agent, or build something new?" Otherwise -> proceed with their original request.

## Configuration

Only 3 pieces of information are needed. Ask for them **one at a time** -- don't batch all questions into a single message. Start with whichever the user hasn't provided yet, wait for the answer, then ask the next. If the user provides multiple details upfront (e.g., "project is foo, voice agent"), skip the questions they already answered.

1. **GCP Project ID** -- which GCP project to use
2. **App name** -- display name for the agent app (also used as app ID)
3. **Modality** -- `audio` (voice agent) or `text` (chat agent)

Everything else is derived:
- **Location**: defaults to `us`
- **Model**: `gemini-3.1-flash-live` for audio, `gemini-3-flash` for text
- **deployed_app_id**: `null` for new apps (set after first push). Note: For `deployed_app_id`, use the **short name** (e.g., `my-app-id`), NOT the full Google Cloud resource path. The SDK handles the pathing automatically.

Once you have all 3, write `<project_name>/gecx-config.json` inside the project folder (NOT at the repo root):
```json
{
  "gcp_project_id": "<project>",
  "location": "us",
  "app_name": "<app_name>",
  "deployed_app_id": null,
  "app_dir": "cxas_app/",
  "model": "<model_based_on_modality>",
  "modality": "<audio_or_text>",
  "default_channel": "<audio_or_text>"
}
```

If the user provides these details upfront (e.g., "build me an agent, project is foo, app name is bar, voice agent"), skip asking and write the config immediately.

