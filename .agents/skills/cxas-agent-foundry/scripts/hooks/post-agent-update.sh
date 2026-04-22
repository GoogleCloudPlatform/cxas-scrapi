#!/bin/bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Reminds to sync callbacks and update tests after agent changes
# Also auto-pulls latest agent state to local files after SCRAPI updates
# Works with both Claude Code and Gemini CLI

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../resolve-project.sh"

input=$(cat)

cmd=$(echo "$input" | jq -r '.tool_input.command // .arguments.command // ""')
agent=$(echo "$input" | jq -r 'if .tool_input then "claude" else "gemini" end')

if echo "$cmd" | grep -qE 'update_agent'; then
  # Auto-pull latest agent state to keep local files in sync
  project_dir=$(resolve_project_dir)
  config_file="${project_dir}/gecx-config.json"
  pull_msg=""
  if [ -n "$project_dir" ] && [ -f "$config_file" ]; then
    app_dir="${project_dir}/$(jq -r '.app_dir // "cxas_app/"' "$config_file")"
    project=$(jq -r '.gcp_project_id' "$config_file")
    location=$(jq -r '.location' "$config_file")
    app_id=$(jq -r '.deployed_app_id' "$config_file")
    app_resource="projects/${project}/locations/${location}/apps/${app_id}"
    if GOOGLE_CLOUD_PROJECT="$project" cxas pull "$app_resource" --project-id "$project" --location "$location" --target-dir "$app_dir" 2>/dev/null; then
      pull_msg="AUTO-SYNC: Pulled latest agent state to $app_dir. "
    fi
  fi

  # Auto-sync callbacks from platform to local test dirs
  sync_msg=""
  if [ -f ".agents/skills/cxas-agent-foundry/scripts/sync-callbacks.py" ]; then
    sync_output=$(python3 .agents/skills/cxas-agent-foundry/scripts/sync-callbacks.py 2>&1) && \
      sync_msg="AUTO-SYNC: Callbacks synced to evals/callback_tests/. " || true
  fi

  msg="${pull_msg}${sync_msg}REMINDER: Agent was updated. Run callback tests to verify, and update TDD changelog."
  if [ "$agent" = "claude" ]; then
    echo "{\"hookSpecificOutput\":{\"hookEventName\":\"PostToolUse\",\"additionalContext\":\"$msg\"}}"
  else
    echo "{\"decision\":\"allow\",\"context_update\":\"$msg\"}"
  fi
else
  if [ "$agent" = "claude" ]; then
    echo '{}'
  else
    echo '{"decision":"allow"}'
  fi
fi
