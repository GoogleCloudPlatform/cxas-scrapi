# Skill: SCRAPI CCAS & Polysynth Integrations

Leverage mounted SCRAPI scripts and Google3 Blaze pipelines to execute deployments and manage credentials natively.

## 1. Albertsons OAuth Token Generation (Local Testing)
To generate a fresh OAuth token for Albertsons dev APIs without hitting the agent context:
- Execute the python script natively:
  `python3 /workspace/scrapi/cloud/ai/fde/customers/albertsons/scripts/get_oauth_token.py`
- The script automatically reads your ~/.secrets stashed client secret safely.

## 2. Import Local configuration changes to Polysynth (Target Dev)
Deploy your local workspace configuration to the Polysynth target app resource:
```bash
blaze run //cloud/ai/fde/tools/agent_operations:polysynth_operations -- \
  --operation import_to_polysynth \
  --environment prod \
  --skip_version_creation \
  --app_resource_name "projects/ces-deployment-dev/locations/us/apps/<YOUR_APP_ID>" \
  --env_json_filename "environment_albertsons_dev.json" \
  --app_google3_dir /workspace/scrapi/cloud/ai/fde/customers/albertsons/app
```

## 3. Sync canonical formatting back to workspace (Export)
Export the latest configurations and formatting from Polysynth back to your workspace:
```bash
blaze run //cloud/ai/fde/tools/agent_operations:polysynth_operations -- \
  --operation export_from_polysynth \
  --app_resource_name "projects/ces-deployment-dev/locations/us/apps/<YOUR_APP_ID>" \
  --app_google3_dir /workspace/scrapi/cloud/ai/fde/customers/albertsons/app
```
- Execute the synchronization loop (Import -> Export -> Review g4 diff -> Commit CL) to publish your changes cleanly!
