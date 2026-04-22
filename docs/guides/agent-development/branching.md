---
title: Branching Apps
description: How to use cxas branch to clone an app for staging or PR testing.
---

# Branching Apps

`cxas branch` creates a new CX Agent Studio app by pulling an existing app's configuration and immediately pushing it to a fresh app. Think of it as cloning — you get a complete, independent copy of an app that you can experiment with, test against, or use as a staging environment.

---

## How branching works

The branch command is a three-step operation:

1. **Pull** the source app's current configuration
2. **Create** a new, empty app on the platform with the name you specify
3. **Push** the pulled configuration into the new app

Because the new app is independent of the source, changes you make to the branch don't affect the original — and vice versa.

```bash
cxas branch <source_app> <new_app_id> [--project_id PROJECT] [--location LOCATION]
```

---

## Use cases

### Staging environment

The most common use case is maintaining a staging version of your production agent. You push all changes to staging first, test thoroughly, and then promote to production.

```bash
# Create a staging branch from production
cxas branch \
  "projects/my-project/locations/us-central1/apps/my-app-production" \
  my-app-staging \
  --project_id my-project \
  --location us-central1
```

You now have `my-app-staging` as an independent copy. Develop on staging, then when you're ready to release, use `cxas push --to` to promote:

```bash
# Pull staging to local
cxas pull "my-app-staging" --project_id my-project --location us-central1

# ... make changes, test ...

# Push to production
cxas push cxas_app/my-app-staging \
  --to projects/my-project/locations/us-central1/apps/my-app-production
```

### PR testing

When reviewing a pull request that changes agent behavior, you want to test the proposed changes in isolation without touching the shared staging environment.

```bash
# Create a branch for PR #42
cxas branch \
  "projects/my-project/locations/us-central1/apps/my-app-staging" \
  my-app-pr-42 \
  --project_id my-project \
  --location us-central1

# Pull the PR's changes locally
git checkout feature/pr-42
cxas pull my-app-pr-42 --project_id my-project --location us-central1

# Apply the PR's local changes
# ... apply changes from the PR diff ...

# Push to the PR branch
cxas push cxas_app/my-app-pr-42
```

After testing, the CI/CD system (or a reviewer) can clean up the PR branch:

```bash
# Clean up the PR branch after merge
cxas delete app my-app-pr-42 --project_id my-project --location us-central1
```

### `cxas ci-test` and automatic branching

The `cxas ci-test` command uses this pattern automatically. It creates a temporary branch with a deterministic name (based on the branch name and commit SHA), runs all tests against it, reports results, and then deletes the branch.

---

## Full example: staging workflow

Here's a complete example of using branching as part of an everyday development workflow.

```bash
# --- Initial setup (one time) ---

# Create production app
cxas create "My App" --app_name my-app --project_id my-project --location us

# Create staging by branching from production
cxas branch \
  projects/my-project/locations/us-central1/apps/my-app \
  my-app-staging \
  --project_id my-project \
  --location us-central1

# --- Development loop ---

# Pull staging to work locally
cxas pull my-app-staging --project_id my-project --location us-central1 --target_dir .

# Make changes to instruction or tools
# ...

# Lint and push to staging
cxas lint
cxas push cxas_app/my-app-staging

# Test on staging
cxas test-tools --app my-app-staging --file evals/tool_tests.yaml
cxas push-eval --app my-app-staging --file evals/goldens.yaml
cxas run my-app-staging --wait

# --- Release ---

# Push staging config to production
cxas push cxas_app/my-app-staging \
  --to projects/my-project/locations/us-central1/apps/my-app
```

---

## Python API equivalent

If you prefer to script branching:

```python
from cxas_scrapi.core.apps import Apps
import tempfile
import subprocess

project_id = "my-project"
location = "us-central1"

apps = Apps(project_id=project_id, location=location)

# 1. Export the source app
source_app_name = f"projects/{project_id}/locations/{location}/apps/my-app"
with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
    apps.export_app(source_app_name, local_path=tmp.name)
    zip_path = tmp.name

# 2. Import as a new app
new_app = apps.import_as_new_app(
    display_name="My App Staging",
    local_path=zip_path,
)

print(f"Branch created: {new_app.name}")
```

---

## Tips and considerations

**Branch names must be unique within a project**
: If you try to create a branch with an ID that already exists, the command will fail. Use descriptive, collision-resistant names like `my-app-staging` or `my-app-pr-42`.

**Branching copies the draft, not a version**
: The branch is created from the app's current draft state. If you want to branch from a specific version, restore that version first, then branch.

**Deployments are not copied**
: The branch gets the app configuration but not its deployments. You'll need to create deployments on the branch app separately if you want to test in a deployed context.

**Cost consideration**
: Each branch is a full, independent app on the platform. Unused branches still count toward your resource quotas. Clean up PR branches after merge.
