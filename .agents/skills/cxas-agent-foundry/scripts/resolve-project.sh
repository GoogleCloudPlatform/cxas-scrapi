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

# Resolves the active project directory for GECX hooks and shell scripts.
# Source this file, then call resolve_project_dir.
#
# Usage:
#   source "$(dirname "$0")/../../.agents/skills/cxas-agent-foundry/scripts/resolve-project.sh"
#   project_dir=$(resolve_project_dir)
#   config_file="${project_dir}/gecx-config.json"

resolve_project_dir() {
  # Find workspace root (contains .agents/, .claude/, or .gemini/)
  local workspace_root
  workspace_root="$(pwd)"
  local _candidate
  _candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  for _ in 1 2 3 4 5; do
    _candidate="$(cd "$_candidate/.." && pwd)"
    if [ -d "$_candidate/.agents" ] || [ -d "$_candidate/.claude" ] || [ -d "$_candidate/.gemini" ]; then
      workspace_root="$_candidate"
      break
    fi
  done

  # 1. GECX_PROJECT env var
  if [ -n "${GECX_PROJECT:-}" ]; then
    local candidate="${workspace_root}/${GECX_PROJECT}"
    if [ -f "${candidate}/gecx-config.json" ]; then
      echo "${candidate}"
      return 0
    fi
    echo "Error: GECX_PROJECT=${GECX_PROJECT} but ${candidate}/gecx-config.json not found." >&2
    return 1
  fi

  # 2. CWD has gecx-config.json (backward compat)
  if [ -f "gecx-config.json" ]; then
    echo "$(pwd)"
    return 0
  fi

  # 3. .active-project pointer
  if [ -f "${workspace_root}/.active-project" ]; then
    local name
    name=$(cat "${workspace_root}/.active-project" | tr -d '[:space:]')
    if [ -n "${name}" ] && [ -f "${workspace_root}/${name}/gecx-config.json" ]; then
      echo "${workspace_root}/${name}"
      return 0
    fi
  fi

  # 4. Auto-detect single project
  local projects=()
  for dir in "${workspace_root}"/*/; do
    if [ -f "${dir}gecx-config.json" ]; then
      projects+=("${dir%/}")
    fi
  done

  if [ ${#projects[@]} -eq 1 ]; then
    echo "${projects[0]}"
    return 0
  fi

  # No project found
  return 1
}
