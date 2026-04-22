---
title: Linting in CI
description: Integrate the CXAS linter into GitHub Actions, pre-push hooks, and zero-warnings policies.
---

# Linting in CI

Running the linter in CI ensures that every pull request is checked against the same standards as local development. This page covers integrating `cxas lint` into GitHub Actions, using the `--json` output for reporting, setting up a pre-push hook, and enforcing a zero-errors policy.

---

## GitHub Actions

Here's a minimal GitHub Actions step that runs the linter on every push and pull request:

```yaml
name: CXAS Lint

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write  # Required for Workload Identity Federation

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install SCRAPI
        run: pip install cxas-scrapi

      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}

      - name: Pull app for linting
        run: |
          cxas pull "${{ vars.APP_NAME }}" \
            --project_id "${{ vars.GCP_PROJECT_ID }}" \
            --location "${{ vars.GCP_LOCATION }}"

      - name: Run linter
        run: cxas lint --json > lint-results.json || true

      - name: Check for errors
        run: |
          ERRORS=$(python -c "
          import json, sys
          with open('lint-results.json') as f:
              results = json.load(f)
          errors = [r for r in results if r['severity'] == 'error']
          print(len(errors))
          ")
          if [ "$ERRORS" -gt 0 ]; then
            echo "Linting failed with $ERRORS error(s)"
            cat lint-results.json | python -c "
          import json, sys
          for r in json.load(sys.stdin):
              if r['severity'] == 'error':
                  print(f\"  [{r['rule_id']}] {r['file']}: {r['message']}\")
          "
            exit 1
          else
            echo "Linting passed"
          fi

      - name: Upload lint results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: lint-results
          path: lint-results.json
```

### Simpler approach: use the exit code directly

If you don't need the JSON report, the simplest approach is to rely on the exit code:

```yaml
- name: Run linter (fail on errors)
  run: cxas lint
  # Exit code 0 = no errors; 1 = errors found
```

`cxas lint` exits with `1` if any `error`-severity rule fires, and `0` otherwise. Warnings and info messages don't affect the exit code.

---

## JSON output format

The `--json` flag produces a JSON array where each element is a lint result:

```bash
cxas lint --json
```

```json
[
  {
    "file": "agents/support-root/instruction.txt",
    "line": null,
    "severity": "error",
    "rule_id": "I001",
    "message": "Missing required XML tag: <role>",
    "fix_suggestion": "Add <role>...</role> section to instruction"
  },
  {
    "file": "tools/lookup_order/python_function/python_code.py",
    "line": 5,
    "severity": "warning",
    "rule_id": "T002",
    "message": "Missing docstring — CES uses tool docstrings for invocation routing",
    "fix_suggestion": "Add a descriptive docstring explaining when and how the LLM should use this tool"
  }
]
```

This format is easy to consume in CI scripts, dashboards, or reporting tools.

---

## Pre-push hook

The skills system installs a pre-push hook (`pre-agent-push-lint.sh`) that runs the linter before every `cxas push`. Here's what that hook looks like, and how to install it manually if you're not using the skills system:

```bash
#!/bin/bash
# .agents/hooks/pre-agent-push-lint.sh
# Runs cxas lint before push; blocks if there are errors.

set -e

echo "Running CXAS linter..."
cxas lint

if [ $? -ne 0 ]; then
  echo "Linting failed. Push blocked."
  echo "Fix the errors above and try again."
  exit 1
fi

echo "Linting passed. Proceeding with push."
```

To install this as a Git hook that runs before any push:

```bash
# Copy the hook
cp .agents/hooks/pre-agent-push-lint.sh .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

Or, if you want it to run only before `cxas push` (not all pushes), the skills system registers it with Claude Code's hooks mechanism in `.claude/settings.json` rather than as a Git hook.

---

## Zero-errors policy

A zero-errors policy means the CI pipeline fails if *any* `error`-severity lint rule fires. This is the recommended approach — errors in SCRAPI lint indicate issues that will almost certainly cause problems on the platform.

To implement this, use `cxas lint` with no additional flags in your CI step. The exit code tells the story:

| Exit code | Meaning | CI result |
|-----------|---------|-----------|
| 0 | No errors | Pass |
| 1 | One or more errors | Fail |

If you want to also fail on warnings (stricter policy), you can use `--json` and check the severity:

```bash
# Fail on any error or warning
cxas lint --json | python -c "
import json, sys
results = json.load(sys.stdin)
bad = [r for r in results if r['severity'] in ('error', 'warning')]
if bad:
    for r in bad:
        print(f\"  [{r['severity'].upper()}] [{r['rule_id']}] {r['file']}: {r['message']}\")
    sys.exit(1)
"
```

---

## Suppressing specific rules in CI

Sometimes you want to suppress a rule only in CI (e.g., a rule that requires a network call to validate). Use a CI-specific `cxaslint.yaml`:

```yaml
# cxaslint-ci.yaml
app_dir: cxas_app/My Support Agent
evals_dir: evals

rules:
  # Disable rules that require platform access in CI
  E003: off   # Tool existence check requires API call
  V001: off   # Schema validation can be slow

  # Be strict about everything else
  I001: error
  T001: error
  C001: error
```

```bash
cxas lint --config cxaslint-ci.yaml
```

---

## Integrating lint results with GitHub Pull Request comments

You can post lint results as PR comments using the GitHub CLI:

```yaml
- name: Post lint results as PR comment
  if: github.event_name == 'pull_request' && failure()
  run: |
    COMMENT=$(python -c "
    import json
    with open('lint-results.json') as f:
        results = json.load(f)
    errors = [r for r in results if r['severity'] == 'error']
    if not errors:
        print('No lint errors.')
    else:
        lines = ['**CXAS Lint Errors:**', '']
        for r in errors:
            lines.append(f\"- \`[{r['rule_id']}]\` \`{r['file']}\`: {r['message']}\")
        print('\\n'.join(lines))
    ")
    gh pr comment ${{ github.event.pull_request.number }} --body "\$COMMENT"
  env:
    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```
