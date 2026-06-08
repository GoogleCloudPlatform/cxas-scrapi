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

# Drift detection before pushing local agent code to CXAS
# Blocks the push if local files are stale (platform has changes not in local)
# Also validates the push target matches gecx-config.json
# Works with both Claude Code and Gemini CLI

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../resolve-project.sh"

input=$(cat)

agent=$(echo "$input" | jq -r 'if .tool_input then "claude" else "gemini" end')
cmd=$(echo "$input" | jq -r '.tool_input.command // .arguments.command // ""')

# Only act if running cxas push (or legacy cxas-eval push)
if echo "$cmd" | grep -qE 'cxas(-eval)? push'; then
  project_dir=$(resolve_project_dir)
  if [ -z "$project_dir" ]; then
    if [ "$agent" = "claude" ]; then
      echo '{}'
    else
      echo '{"decision":"allow"}'
    fi
    exit 0
  fi

  local config_file="${project_dir}/gecx-config.toml"
  local is_toml=true
  if [ ! -f "$config_file" ]; then
    config_file="${project_dir}/gecx-config.json"
    is_toml=false
  fi

  if [ ! -f "$config_file" ]; then
    if [ "$agent" = "claude" ]; then
      echo '{}'
    else
      echo '{"decision":"allow"}'
    fi
    exit 0
  fi

  local app_dir_val
  local project
  local location
  local deployed_app_id

  if [ "$is_toml" = true ]; then
    app_dir_val=$(parse_toml_key "app-dir" "$config_file")
    project=$(parse_toml_key "gcp-project-id" "$config_file")
    location=$(parse_toml_key "location" "$config_file")
    deployed_app_id=$(parse_toml_key "deployed-app-id" "$config_file")
  else
    app_dir_val=$(jq -r '.app_dir // "cxas_app/"' "$config_file")
    project=$(jq -r '.gcp_project_id' "$config_file")
    location=$(jq -r '.location' "$config_file")
    deployed_app_id=$(jq -r '.deployed_app_id' "$config_file")
  fi

  : "${app_dir_val:=cxas_app/}"
  app_dir="${project_dir}/${app_dir_val}"
  project=$(echo "$project" | tr -d '[:space:]')
  location=$(echo "$location" | tr -d '[:space:]')
  deployed_app_id=$(echo "$deployed_app_id" | tr -d '[:space:]')
  app_resource="projects/${project}/locations/${location}/apps/${deployed_app_id}"

  # Validate push target matches config
  if echo "$cmd" | grep -q -- "--to"; then
    push_target=$(echo "$cmd" | grep -oP '(?<=--to\s)\S+' || echo "")
    if [ -n "$push_target" ] && [ "$push_target" != "$deployed_app_id" ] && [ "$push_target" != "$app_resource" ]; then
      msg="WARNING: Push target ($push_target) does not match deployed_app_id ($deployed_app_id) in gecx-config.json. Verify you are pushing to the correct app."
      if [ "$agent" = "claude" ]; then
        echo "{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"additionalContext\":\"$msg\"}}"
      else
        echo "{\"decision\":\"allow\",\"context_update\":\"$msg\"}"
      fi
      exit 0
    fi
  fi

  # Drift detection: pull platform state to temp dir and compare
  if [ -d "$app_dir" ]; then
    tmp_dir=$(mktemp -d)
    trap 'rm -rf "$tmp_dir"' EXIT

    if GOOGLE_CLOUD_PROJECT="$project" cxas pull "$app_resource" --project-id "$project" --location "$location" --target-dir "$tmp_dir" 2>/dev/null; then
      # Compare platform state vs local state
      drift=$(diff -rq "$tmp_dir" "$app_dir" 2>/dev/null || true)
      if [ -n "$drift" ]; then
        msg="BLOCKED: Platform state has diverged from local files in $app_dir. Someone made changes via SCRAPI or the UI that are not in your local copy. Run 'cxas pull $app_resource --project-id $project --location $location --target-dir $app_dir' to merge platform changes first, then retry the push."
        if [ "$agent" = "claude" ]; then
          echo "{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"blockToolExecution\":true,\"additionalContext\":\"$msg\"}}"
        else
          echo "{\"decision\":\"block\",\"context_update\":\"$msg\"}"
        fi
        exit 0
      fi
    fi
  fi

  # No drift detected, allow the push
  if [ "$agent" = "claude" ]; then
    echo '{}'
  else
    echo '{"decision":"allow"}'
  fi
else
  if [ "$agent" = "claude" ]; then
    echo '{}'
  else
    echo '{"decision":"allow"}'
  fi
fi
