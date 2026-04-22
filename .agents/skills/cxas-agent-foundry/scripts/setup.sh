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

# Sets up the local development environment for GECX agent development.
# Creates a Python virtualenv, installs cxas-scrapi from the local codebase,
# and runs the interactive configuration wizard to create gecx-config.json.
#
# Usage:
#   .agents/skills/cxas-agent-foundry/scripts/setup.sh                  # Full setup (install + configure)
#   .agents/skills/cxas-agent-foundry/scripts/setup.sh --configure      # Skip install, run configuration wizard only
#   .agents/skills/cxas-agent-foundry/scripts/setup.sh --skip-config    # Install only, skip configuration wizard

set -euo pipefail

# Resolve paths relative to the script location.
# Supports two layouts:
#   1. cxas-scrapi/skills/cxas-agent-foundry/scripts/setup.sh  (skill inside scrapi)
#   2. .agents/skills/cxas-agent-foundry/scripts/setup.sh       (skill alongside scrapi)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Find cxas-scrapi: walk up from SKILL_ROOT looking for setup.py with cxas-scrapi
SCRAPI_DIR=""
candidate="$SKILL_ROOT"
for _ in 1 2 3 4 5; do
  candidate="$(cd "$candidate/.." && pwd)"
  if [ -f "$candidate/setup.py" ] && grep -q "cxas.scrapi\|cxas-scrapi" "$candidate/setup.py" 2>/dev/null; then
    SCRAPI_DIR="$candidate"
    break
  fi
  # Also check for cxas-scrapi/ as a sibling directory
  if [ -d "$candidate/cxas-scrapi" ] && [ -f "$candidate/cxas-scrapi/setup.py" ]; then
    SCRAPI_DIR="$candidate/cxas-scrapi"
    break
  fi
done

# Workspace root is where .venv and project folders live
# If skill is inside scrapi, workspace is scrapi's parent; otherwise walk up
# from the skill root looking for .agents/, .claude/, or .gemini/ to find the workspace.
if [ -n "$SCRAPI_DIR" ] && [[ "$SKILL_ROOT" == "$SCRAPI_DIR"* ]]; then
  WORKSPACE_ROOT="$(cd "$SCRAPI_DIR/.." && pwd)"
else
  # Walk up from SKILL_ROOT looking for the directory that contains .agents/
  _candidate="$SKILL_ROOT"
  WORKSPACE_ROOT=""
  for _ in 1 2 3 4 5; do
    _candidate="$(cd "$_candidate/.." && pwd)"
    if [ -d "$_candidate/.agents" ] || [ -d "$_candidate/.claude" ] || [ -d "$_candidate/.gemini" ]; then
      WORKSPACE_ROOT="$_candidate"
      break
    fi
  done
  if [ -z "$WORKSPACE_ROOT" ]; then
    # Fallback: 3 levels up from skill root (.agents/skills/cxas-agent-foundry -> workspace)
    WORKSPACE_ROOT="$(cd "$SKILL_ROOT/../../.." && pwd)"
  fi
fi
cd "$WORKSPACE_ROOT"

SKIP_CONFIG=false
CONFIGURE_ONLY=false
VENV_DIR=".venv"

# Parse arguments
for arg in "$@"; do
  case $arg in
    --skip-config)
      SKIP_CONFIG=true
      ;;
    --configure)
      CONFIGURE_ONLY=true
      ;;
  esac
done

echo "========================================="
echo " GECX Development Environment Setup"
echo "========================================="
echo ""

# --- Check if we can skip install ---
if [ "$CONFIGURE_ONLY" = false ]; then
  NEEDS_INSTALL=true

  # If venv exists, check if cxas-scrapi is already installed
  if [ -d "$VENV_DIR" ]; then
    source "${VENV_DIR}/bin/activate"
    if python -c "import cxas_scrapi" 2>/dev/null; then
      installed_ver=$(python -c "import importlib.metadata; print(importlib.metadata.version('cxas-scrapi'))" 2>/dev/null || echo "unknown")
      echo "  cxas-scrapi v${installed_ver} is already installed."
      echo ""

      reinstall=$(bash -c 'read -p "  Reinstall from local source? [y/N] " ans; echo "$ans"')
      if [[ ! "$reinstall" =~ ^[Yy] ]]; then
        NEEDS_INSTALL=false
      fi
    fi
  fi

  if [ "$NEEDS_INSTALL" = true ]; then
    # --- Step 1: Create virtualenv ---
    if [ ! -d "$VENV_DIR" ]; then
      echo "[1/3] Creating virtualenv in ${VENV_DIR}..."
      python3 -m venv "$VENV_DIR"
    else
      echo "[1/3] Virtualenv already exists at ${VENV_DIR}"
    fi

    source "${VENV_DIR}/bin/activate"

    # --- Step 2: Install cxas-scrapi from local source ---
    if [ -z "$SCRAPI_DIR" ] || [ ! -f "$SCRAPI_DIR/setup.py" ]; then
      echo "[2/3] Error: Could not find cxas-scrapi source."
      echo "      Looked relative to skill at: $SKILL_ROOT"
      exit 1
    fi

    echo "[2/3] Installing cxas-scrapi from $SCRAPI_DIR..."
    pip install -e "$SCRAPI_DIR" --quiet
    pip install rich InquirerPy --quiet

    echo ""
    installed_ver=$(python -c "import importlib.metadata; print(importlib.metadata.version('cxas-scrapi'))" 2>/dev/null || echo "unknown")
    echo "      cxas-scrapi v${installed_ver} installed."
    echo ""
  else
    # Ensure rich + InquirerPy are installed even if scrapi was already there
    pip install rich InquirerPy --quiet 2>/dev/null
    echo ""
  fi
else
  # --configure only: just activate the venv
  if [ ! -d "$VENV_DIR" ]; then
    echo "Error: No virtualenv found at ${VENV_DIR}. Run setup.sh first (without --configure)."
    exit 1
  fi
  source "${VENV_DIR}/bin/activate"
  pip install rich InquirerPy --quiet 2>/dev/null
fi

# --- Step 3: Run configuration wizard ---
if [ "$SKIP_CONFIG" = false ]; then
  echo "[3/3] Running configuration wizard..."
  echo ""
  python "$SCRIPT_DIR/configure.py"
else
  echo "[3/3] Skipping configuration wizard (--skip-config)."
fi

echo ""
echo "========================================="
echo " Setup complete!"
echo ""
echo " Activate the virtualenv with:"
echo "   source ${VENV_DIR}/bin/activate"
echo "========================================="
