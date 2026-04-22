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

import argparse
import os
import re
import stat
import subprocess

import yaml

from cxas_scrapi.core.common import Common

"""Templates used by the CXAS SCRAPI CLI."""

GITHUB_ACTION_TEMPLATE_TEST = """name: "CI Test {agent_name}"

on:
  pull_request:
    paths:
      - '{path_filter}/*.yaml'
      - '{path_filter}/*.yml'
      - '{path_filter}/*.json'
  workflow_call:

env:
  PROJECT_ID: "{project_id}"
  LOCATION: "{location}"
{auth_env}

jobs:
  test-{agent_name_lower}:
    runs-on: ubuntu-latest

    permissions:
      contents: 'read'
      id-token: 'write'

    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Free Disk Space (Ubuntu)
        uses: jlumbroso/free-disk-space@main
        with:
          tool-cache: false
          android: true
          dotnet: true
          haskell: true
          large-packages: true
          docker-images: true
          swap-storage: true

{auth_step}

{setup_gcloud_step}

      - name: Download cxas-scrapi CLI Wheel
        run: |
          gsutil cp gs://cxas-scrapi-github/cxas_scrapi-0.1.3-py3-none-any.whl \
            {github_context_path}/

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build Docker Image
        uses: docker/build-push-action@v5
        with:
          context: {github_context_path}
          load: true
          tags: agent-image
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Run CI Test Lifecycle (Docker)
        run: |
          D="[CI] PR-"
          D="$D${{{{ github.event.pull_request.number }}}} {agent_name}"
          docker run --rm \\
            -v ${{{{ github.workspace }}}}:/workspace \\
            -w /workspace \\
            -e PROJECT_ID=${{{{ env.PROJECT_ID }}}} \\
            -e LOCATION=${{{{ env.LOCATION }}}} \\
{docker_auth_args}
            agent-image \\
            ci-test --app-dir {github_context_path} \\
                      --project_id ${{{{ env.PROJECT_ID }}}} \\
                      --location ${{{{ env.LOCATION }}}} \\
                      --display_name "$D"

"""

GITHUB_ACTION_TEMPLATE_DEPLOY = """name: "Deploy {agent_name}"

on:
  push:
    branches:
      - {target_branch}
    paths:
      - '{path_filter}/*.yaml'
      - '{path_filter}/*.yml'
      - '{path_filter}/*.json'

env:
  PROJECT_ID: "{project_id}"
  LOCATION: "{location}"
  APP_ID: "{app_id}"
  DISPLAY_NAME: "{agent_name}"
{auth_env}

permissions:
  contents: 'read'
  id-token: 'write'

jobs:
  test-{agent_name_lower}:
    uses: ./.github/workflows/ci_test_{agent_name_lower}.yml
    secrets: inherit

  deploy-{agent_name_lower}:
    needs: test-{agent_name_lower}
    runs-on: ubuntu-latest

    permissions:
      contents: 'read'
      id-token: 'write'

    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

{auth_step}

{setup_gcloud_step}

      - name: Install cxas-scrapi CLI
        run: |
          python -m pip install --upgrade pip
          wget https://storage.googleapis.com/gassets-api-ai/ces-client-libraries/v1beta/ces-v1beta-py.tar
          pip install ces-v1beta-py.tar --quiet
          gsutil cp gs://cxas-scrapi-github/cxas_scrapi-0.1.3-py3-none-any.whl .
          pip install cxas_scrapi-0.1.3-py3-none-any.whl

      - name: Deploy to CX Agent Studio
        run: |
          cxas push --app-dir {github_context_path} \\
                         --project_id ${{{{ env.PROJECT_ID }}}} \\
                         --location ${{{{ env.LOCATION }}}} \\
                         --app_id ${{{{ env.APP_ID }}}} \\
                         --display_name "${{{{ env.DISPLAY_NAME }}}}"
"""

GITHUB_ACTION_TEMPLATE_CLEANUP = """name: "Cleanup {agent_name}"

on:
  pull_request:
    types: [closed]
    paths:
      - '{path_filter}/*.yaml'
      - '{path_filter}/*.yml'
      - '{path_filter}/*.json'

env:
  PROJECT_ID: "{project_id}"
  LOCATION: "{location}"
{auth_env}

jobs:
  cleanup-{agent_name_lower}:
    runs-on: ubuntu-latest
    if: >
      github.event.pull_request.merged == true ||
      github.event.pull_request.closed == true

    permissions:
      contents: 'read'
      id-token: 'write'

    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

{auth_step}

{setup_gcloud_step}

      - name: Install cxas-scrapi CLI
        run: |
          python -m pip install --upgrade pip
          wget https://storage.googleapis.com/gassets-api-ai/ces-client-libraries/v1beta/ces-v1beta-py.tar
          pip install ces-v1beta-py.tar --quiet
          gsutil cp gs://cxas-scrapi-github/cxas_scrapi-0.1.3-py3-none-any.whl .
          pip install cxas_scrapi-0.1.3-py3-none-any.whl

      - name: Run Cleanup
        run: |
          D="[CI] PR-${{{{ github.event.pull_request.number }}}} {agent_name}"
          cxas delete --display_name "$D" \\
                   --project_id ${{{{ env.PROJECT_ID }}}} \\
                   --location ${{{{ env.LOCATION }}}}
"""


DOCKERFILE_TEMPLATE = """# Use an official Python runtime as a parent image
FROM python:3.11-slim


# Set the working directory to /app
WORKDIR /app

# Install git and wget (required for pip install and downloading CES lib)
RUN apt-get update && apt-get install -y git wget && rm -rf /var/lib/apt/lists/*

# Install CES Client Library (Pre-requisite) - Cached Layer
RUN URL="https://storage.googleapis.com/gassets-api-ai/" && \\
    URL="${URL}ces-client-libraries/v1beta/ces-v1beta-py.tar" && \\
    wget $URL && \\
    pip install ces-v1beta-py.tar --quiet && \\
    rm ces-v1beta-py.tar

# Copy the CLI wheel downloaded by GitHub Actions into the container
COPY cxas_scrapi-*.whl /dist/

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install dependencies and local CLI wheel
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install /dist/cxas_scrapi-*.whl

# Copy the agent code into the container
COPY . .

# Set the entrypoint to cxas-scrapi
ENTRYPOINT ["cxas"]
"""


def _get_github_details(agent_dir: str) -> tuple[str | None, str | None]:
    """Infers GitHub Owner and Repo from git remote origin URL."""
    try:
        url = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=agent_dir,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

        if "github.com" in url:
            if url.startswith("git@"):
                path = url.split("github.com:")[1]
            else:
                path = url.split("github.com/")[1]

            if path.endswith(".git"):
                path = path[:-4]

            parts = path.split("/")
            if len(parts) >= 2:
                return parts[0], parts[1]
    except Exception:
        pass
    return None, None


def _auto_setup_wif(
    project_id: str,
    github_owner: str,
    github_repo: str,
    pool_name: str = "github-actions-pool-scrapi",
) -> tuple[str | None, str | None]:
    """Creates WIF Pool, Provider, and Service Account via gcloud."""
    print(
        f"\\n--- Starting Automated WIF Setup for "
        f"{github_owner}/{github_repo} ---",
        flush=True,
    )

    try:
        print("Fetching Project Number...", flush=True)
        project_number = subprocess.check_output(
            [
                "gcloud",
                "projects",
                "describe",
                project_id,
                "--format=value(projectNumber)",
                "--quiet",
            ],
            text=True,
        ).strip()
        print(f"Project Number: {project_number}", flush=True)

        provider_name = "github-provider"
        sa_name = "github-actions-sa"

        print(
            f"Checking/Creating Workload Identity Pool '{pool_name}'...",
            flush=True,
        )
        res = subprocess.run(
            [
                "gcloud",
                "iam",
                "workload-identity-pools",
                "describe",
                pool_name,
                "--location=global",
                f"--project={project_id}",
                "--quiet",
            ],
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if res.returncode != 0:
            subprocess.check_call(
                [
                    "gcloud",
                    "iam",
                    "workload-identity-pools",
                    "create",
                    pool_name,
                    "--location=global",
                    "--display-name=GitHub Actions Pool (SCRAPI)",
                    f"--project={project_id}",
                    "--quiet",
                ]
            )
            print(f"Created Pool {pool_name}", flush=True)
        else:
            print(f"Pool {pool_name} already exists.", flush=True)

        print(f"Checking/Creating Provider '{provider_name}'...", flush=True)
        res = subprocess.run(
            [
                "gcloud",
                "iam",
                "workload-identity-pools",
                "providers",
                "describe",
                provider_name,
                f"--workload-identity-pool={pool_name}",
                "--location=global",
                f"--project={project_id}",
                "--quiet",
            ],
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if res.returncode != 0:
            subprocess.check_call(
                [
                    "gcloud",
                    "iam",
                    "workload-identity-pools",
                    "providers",
                    "create-oidc",
                    provider_name,
                    f"--workload-identity-pool={pool_name}",
                    "--location=global",
                    "--issuer-uri=https://token.actions.githubusercontent.com",
                    "--attribute-mapping="
                    "google.subject=assertion.sub,"
                    "attribute.actor=assertion.actor,"
                    "attribute.repository=assertion.repository,"
                    "attribute.repository_owner=assertion.repository_owner",
                    f"--attribute-condition=attribute.repository_owner == "
                    f"'{github_owner}'",
                    f"--project={project_id}",
                    "--quiet",
                ]
            )
            print(f"Created Provider {provider_name}", flush=True)
        else:
            print(f"Provider {provider_name} already exists.", flush=True)

        print(f"Checking/Creating Service Account '{sa_name}'...", flush=True)
        sa_email = f"{sa_name}@{project_id}.iam.gserviceaccount.com"
        res = subprocess.run(
            [
                "gcloud",
                "iam",
                "service-accounts",
                "describe",
                sa_email,
                f"--project={project_id}",
                "--quiet",
            ],
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if res.returncode != 0:
            subprocess.check_call(
                [
                    "gcloud",
                    "iam",
                    "service-accounts",
                    "create",
                    sa_name,
                    "--display-name=GitHub Actions Service Account (SCRAPI)",
                    f"--project={project_id}",
                    "--quiet",
                ]
            )
            print(f"Created SA {sa_email}", flush=True)
        else:
            print(f"SA {sa_email} already exists.", flush=True)

        print("Binding IAM Policy...", flush=True)
        member = f"principalSet://iam.googleapis.com/projects/{project_number}/locations/global/workloadIdentityPools/{pool_name}/attribute.repository/{github_owner}/{github_repo}"
        subprocess.check_call(
            [
                "gcloud",
                "iam",
                "service-accounts",
                "add-iam-policy-binding",
                sa_email,
                "--role=roles/iam.workloadIdentityUser",
                f"--member={member}",
                f"--project={project_id}",
                "--quiet",
            ]
        )
        print("IAM Policy bound.", flush=True)

        wip = (
            f"projects/{project_number}/locations/global/"
            f"workloadIdentityPools/{pool_name}/providers/{provider_name}"
        )
        return wip, sa_email

    except subprocess.CalledProcessError as e:
        print(f"Error during WIF setup: {e}")
        return None, None


def init_github_action(args: argparse.Namespace) -> None:
    """Handles the 'init-github-action' command."""

    print("Generating GitHub Actions workflow template...", flush=True)

    agent_name = args.agent_name
    app_name = args.app_name

    # Try to extract details from app.yaml if available
    agent_dir = args.app_dir if args.app_dir else "."

    try:
        agent_abs_path = os.path.abspath(agent_dir)
        git_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=agent_abs_path,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

        if os.path.commonpath([git_root, agent_abs_path]) != git_root:
            raise ValueError(
                "Discovered Git root does not encapsulate the agent directory."
            )

    except Exception:
        git_root = os.path.abspath(agent_dir)

    app_yaml_path = os.path.join(agent_dir, "app.yaml")

    if os.path.exists(app_yaml_path):
        try:
            with open(app_yaml_path, "r") as f:
                app_data = yaml.safe_load(f)
                if not agent_name and "displayName" in app_data:
                    agent_name = app_data["displayName"]
                if not app_name and "name" in app_data:
                    app_name = app_data["name"]
        except Exception as e:
            print(f"Warning: Could not parse {app_yaml_path}: {e}")

    # Fallback to defaults
    if not agent_name:
        agent_name = "agent"

    # Extract project_id and location from app_id if it exists
    extracted_project = Common._get_project_id(app_name) if app_name else None
    extracted_location = Common._get_location(app_name) if app_name else None

    project_id = (
        getattr(args, "project_id", None)
        or extracted_project
        or "YOUR_PROJECT_ID"
    )
    location = getattr(args, "location", None) or extracted_location or "global"

    if not app_name:
        app_basename = os.path.basename(os.path.abspath(agent_dir))
        app_name = (
            f"projects/{project_id}/locations/{location}/apps/{app_basename}"
        )
        print(
            f"Warning: No --app-name provided and could not retrieve 'name' "
            f"from {app_yaml_path}."
        )
        print(f"Synthesizing app identifier from directory name: {app_name}")

    output_path = (
        args.output
        if args.output
        else f".github/workflows/test_{agent_name.lower()}.yml"
    )

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    if getattr(args, "auto_create_wif", False):
        github_owner, github_repo = None, None
        if getattr(args, "github_repo", None):
            if "/" in args.github_repo:
                github_owner, github_repo = args.github_repo.split("/", 1)

        if not github_owner or not github_repo:
            print("Inferring GitHub details...")
            # Use absolute git_root we calculated earlier
            github_owner, github_repo = _get_github_details(git_root)

        if not github_owner or not github_repo:
            print(
                "Warning: Could not infer GitHub details. Skipping automated "
                "WIF setup."
            )
        else:
            print(f"Inferred GitHub: {github_owner}/{github_repo}")
            if project_id == "YOUR_PROJECT_ID":
                print(
                    "Warning: Cannot setup WIF with placeholder Project ID. "
                    "Please provide --project_id."
                )
            else:
                pool_name = getattr(
                    args, "wif_pool_name", "github-actions-pool-scrapi"
                )
                auto_wip, auto_sa = _auto_setup_wif(
                    project_id, github_owner, github_repo, pool_name
                )
                if auto_wip and auto_sa:
                    print("Automated WIF setup successful.")
                    args.workload_identity_provider = auto_wip
                    args.service_account = auto_sa
    wip = args.workload_identity_provider
    sa = args.service_account

    if not wip or not sa:
        raise ValueError(
            "Either provide --workload_identity_provider and "
            "--service_account, "
            "or use --auto-create-wif to let the CLI generate them for you."
        )

    # Generate the path string. If they used an absolute path like
    # `/Users/.../pilot`, just use `pilot/**`
    agent_basename = os.path.basename(agent_dir.rstrip(os.sep))
    path_filter = f"{agent_basename}/**" if agent_dir != "." else "**"
    github_context_path = agent_basename if agent_dir != "." else "."

    # Configure auth blocks
    auth_env = (
        f'  GCP_WORKLOAD_IDENTITY_PROVIDER: "{wip}"\n'
        f'  GCP_SERVICE_ACCOUNT: "{sa}"'
    )
    auth_step = (
        "      # Authenticate to Google Cloud via Workload Identity "
        "Federation\n"
        "      # See https://github.com/google-github-actions/auth "
        "for configuration instructions\n"
        "      - name: Authenticate to Google Cloud\n"
        "        id: auth\n"
        "        uses: google-github-actions/auth@v2\n"
        "        with:\n"
        "          workload_identity_provider: "
        "${{ env.GCP_WORKLOAD_IDENTITY_PROVIDER }}\n"
        "          service_account: ${{ env.GCP_SERVICE_ACCOUNT }}"
    )
    setup_gcloud_step = """      - name: Set up Cloud SDK
        uses: google-github-actions/setup-gcloud@v2

      - name: Configure Docker Auth
        run: gcloud auth configure-docker us-central1-docker.pkg.dev"""
    docker_auth_args = (
        "            -e GOOGLE_APPLICATION_CREDENTIALS="
        "/workspace/application_default_credentials.json \\\n"
        "            -v ${{ steps.auth.outputs.credentials_file_path }}:"
        "/workspace/application_default_credentials.json \\"
    )

    safe_agent_name = agent_name.lower().replace(" ", "_")
    safe_agent_name = re.sub(r"[^a-z0-9_-]", "", safe_agent_name)

    test_template = GITHUB_ACTION_TEMPLATE_TEST.format(
        agent_name=agent_name.capitalize(),
        agent_name_lower=safe_agent_name,
        path_filter=path_filter,
        github_context_path=github_context_path,
        project_id=project_id,
        location=location,
        auth_env=auth_env,
        auth_step=auth_step,
        setup_gcloud_step=setup_gcloud_step,
        docker_auth_args=docker_auth_args,
    )

    deploy_template = GITHUB_ACTION_TEMPLATE_DEPLOY.format(
        agent_name=agent_name.capitalize(),
        agent_name_lower=safe_agent_name,
        target_branch=args.branch,
        path_filter=path_filter,
        github_context_path=github_context_path,
        app_id=app_name,
        project_id=project_id,
        location=location,
        auth_env=auth_env,
        auth_step=auth_step,
        setup_gcloud_step=setup_gcloud_step,
    )

    # Workflows directory calculation (git_root is now calculated at the top)

    workflows_dir = os.path.join(git_root, ".github", "workflows")
    os.makedirs(workflows_dir, exist_ok=True)

    test_output_path = (
        args.output
        if args.output
        else os.path.join(workflows_dir, f"ci_test_{safe_agent_name}.yml")
    )
    deploy_output_path = (
        args.output
        if args.output
        else os.path.join(workflows_dir, f"deploy_{safe_agent_name}.yml")
    )

    with open(test_output_path, "w") as f:
        f.write(test_template)

    if not args.output:
        with open(deploy_output_path, "w") as f:
            f.write(deploy_template)

    if not args.no_cleanup:
        cleanup_template = GITHUB_ACTION_TEMPLATE_CLEANUP.format(
            agent_name=agent_name.capitalize(),
            agent_name_lower=safe_agent_name,
            path_filter=path_filter,
            github_context_path=github_context_path,
            project_id=project_id,
            location=location,
            auth_env=auth_env,
            auth_step=auth_step,
            setup_gcloud_step=setup_gcloud_step,
        )
        cleanup_output_path = (
            os.path.join(
                os.path.dirname(args.output), f"cleanup_{safe_agent_name}.yml"
            )
            if args.output
            else os.path.join(workflows_dir, f"cleanup_{safe_agent_name}.yml")
        )

        with open(cleanup_output_path, "w") as f:
            f.write(cleanup_template)
        print(f"Generated cleanup workflow: {cleanup_output_path}")

    # Generate Dockerfile if it doesn't exist

    dockerfile_path = os.path.join(agent_dir, "Dockerfile")
    if not os.path.exists(dockerfile_path):
        print(f"Generating Dockerfile at {dockerfile_path}...")
        with open(dockerfile_path, "w") as f:
            f.write(DOCKERFILE_TEMPLATE)
    else:
        print(
            f"Dockerfile already exists at {dockerfile_path}. "
            f"Skipping generation."
        )

    # Generate requirements.txt if it doesn't exist
    requirements_path = os.path.join(agent_dir, "requirements.txt")
    if not os.path.exists(requirements_path):
        print(f"Generating requirements.txt at {requirements_path}...")
        with open(requirements_path, "w") as f:
            f.write("# Add your agent dependencies here\n")
            f.write("# google-cloud-ces  # Uncomment if needed\n")
    else:
        print(
            f"requirements.txt already exists at {requirements_path}. "
            f"Skipping generation."
        )

    print(
        f"Successfully generated GitHub Actions workflows to "
        f"{os.path.dirname(test_output_path)}"
    )

    # Generate run_tests_docker.sh for streamlined auth locally and on runner
    script_path = os.path.join(git_root, "run_tests_docker.sh")
    print(f"Generating helper script at {script_path}...")

    script_content = r"""#!/bin/sh
# Auto-generated by cxas-scrapi for local/CI Docker unified execution

AGENT_DIR=$1
PROJECT_ID=$2
LOCATION=${3:-us}

if [ -z "$AGENT_DIR" ] || [ -z "$PROJECT_ID" ]; then
  echo "Usage: $0 <agent_dir> <project_id> [location]"
  exit 1
fi

ADC_FILE_HOST=""

if [ ! -z "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
  ADC_FILE_HOST="$GOOGLE_APPLICATION_CREDENTIALS"
elif [ -f "$HOME/.config/gcloud/application_default_credentials.json" ]; then
  ADC_FILE_HOST="$HOME/.config/gcloud/application_default_credentials.json"
fi

if [ -z "$ADC_FILE_HOST" ]; then
  echo "Error: No application default credentials found."
  echo "Run 'gcloud auth application-default login' locally."
  exit 1
fi

echo "Downloading cxas-scrapi CLI Wheel..."
gsutil cp gs://cxas-scrapi-github/cxas_scrapi-*.whl \
  "$(dirname "$0")/$AGENT_DIR/"

echo "Building Docker Image..."
docker build -t agent-image "$(dirname "$0")/$AGENT_DIR"

echo "Running Docker Container..."
docker run --rm \
  -v "$(dirname "$0")":/workspace \
  -w /workspace \
  -e PROJECT_ID="$PROJECT_ID" \
  -e LOCATION="$LOCATION" \
  -e GOOGLE_CLOUD_PROJECT="$PROJECT_ID" \
  -e GOOGLE_APPLICATION_CREDENTIALS=\
/workspace/application_default_credentials.json \
  -v "$ADC_FILE_HOST":/workspace/application_default_credentials.json \
  agent-image \
  ci-test --app-dir "$AGENT_DIR" \
            --project_id "$PROJECT_ID" \
            --location "$LOCATION" \
            --display_name "[Local] $(basename "$AGENT_DIR")"
"""
    with open(script_path, "w") as f:
        f.write(script_content)
    os.chmod(script_path, 0o755)

    if args.install_hook:
        hook_path = os.path.join(git_root, ".git", "hooks", "pre-push")
        git_dir = os.path.join(git_root, ".git")
        if os.path.exists(git_dir):
            os.makedirs(os.path.dirname(hook_path), exist_ok=True)
            print(f"Installing pre-push hook at {hook_path}...")

            hook_content = f"""#!/bin/sh
# CXAS SCRAPI Auto-generated Hook
echo "Running local tests before push..."
cxas local-test --app-dir "{agent_dir}" \
  --project_id "{project_id}" --location "{location}"
"""
            with open(hook_path, "w") as f:
                f.write(hook_content)

            # Make executable
            st = os.stat(hook_path)
            os.chmod(hook_path, st.st_mode | stat.S_IEXEC)
            print("Pre-push hook installed successfully.")
        else:
            print(
                "Warning: Not a git repository root. Skipping hook "
                "installation."
            )
