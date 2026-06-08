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

# Extract top-level key from a TOML file (ignores sections/profiles)
parse_toml_key() {
  local key="$1"
  local file="$2"
  if [ -f "$file" ]; then
    awk -F'[ =]+' -v target_key="$key" '
      # Stop parsing if we reach a section header to avoid key collisions
      /^\[/ { exit }
      # Match target key and extract its quoted value
      $1 == target_key {
        val = $0
        sub(/^[^"=]*=[ \t]*/, "", val)
        gsub(/^[ \t]*[\042\047]|[\042\047][ \t]*$/, "", val)
        print val
        exit
      }
    ' "$file"
  fi
}

resolve_project_dir() {
  # Find workspace root (contains .scrapi/, .agents/, .claude/, or .gemini/)
  local workspace_root
  workspace_root="$(pwd)"
  local _candidate
  _candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  for _ in 1 2 3 4 5; do
    _candidate="$(cd "$_candidate/.." && pwd)"
    if [ -d "$_candidate/.scrapi" ] || [ -d "$_candidate/.agents" ] || [ -d "$_candidate/.claude" ] || [ -d "$_candidate/.gemini" ] || [ -f "$_candidate/.active-project" ]; then
      workspace_root="$_candidate"
      break
    fi
  done

  local resolved_dir=""

  # 1. GECX_PROJECT env var
  if [ -n "${GECX_PROJECT:-}" ]; then
    local candidate="${workspace_root}/${GECX_PROJECT}"
    if [ -f "${candidate}/gecx-config.toml" ] || [ -f "${candidate}/gecx-config.json" ]; then
      echo "${candidate}"
      return 0
    fi
    echo "Error: GECX_PROJECT=${GECX_PROJECT} but config not found in ${candidate}" >&2
    return 1
  fi

  # 2. Check modern .scrapi/active-project pointer (TOML)
  if [ -f "${workspace_root}/.scrapi/active-project" ]; then
    local base_dir
    base_dir=$(parse_toml_key "base-dir" "${workspace_root}/.scrapi/active-project")
    if [ -n "${base_dir}" ]; then
      if [[ "$base_dir" = /* ]]; then
        resolved_dir="${base_dir}"
      else
        resolved_dir="${workspace_root}/${base_dir}"
      fi
    fi
  fi

  # 3. Check legacy .active-project pointer (flat string or JSON)
  if [ -z "${resolved_dir}" ] && [ -f "${workspace_root}/.active-project" ]; then
    local content
    content=$(cat "${workspace_root}/.active-project" | tr -d '[:space:]')
    # If JSON, parse it
    if [[ "$content" = \{* ]]; then
      local base_dir
      base_dir=$(parse_toml_key "base-dir" "${workspace_root}/.active-project")
      if [ -n "${base_dir}" ]; then
        if [[ "$base_dir" = /* ]]; then
          resolved_dir="${base_dir}"
        else
          resolved_dir="${workspace_root}/${base_dir}"
        fi
      fi
    else
      # Flat relative path string
      if [ -n "${content}" ]; then
        resolved_dir="${workspace_root}/${content}"
      fi
    fi
  fi

  # Validate and return
  if [ -n "${resolved_dir}" ]; then
    local abs_dir
    abs_dir="$(cd "${resolved_dir}" && pwd 2>/dev/null)"
    if [ -f "${abs_dir}/gecx-config.toml" ] || [ -f "${abs_dir}/gecx-config.json" ]; then
      echo "${abs_dir}"
      return 0
    fi
  fi

  # 4. Auto-detect single project
  local projects=()
  for dir in "${workspace_root}"/*/; do
    if [ -f "${dir}gecx-config.toml" ] || [ -f "${dir}gecx-config.json" ]; then
      projects+=("${dir%/}")
    fi
  done

  if [ ${#projects[@]} -eq 1 ]; then
    echo "$(cd "${projects[0]}" && pwd 2>/dev/null)"
    return 0
  fi

  # No project found
  return 1
}
