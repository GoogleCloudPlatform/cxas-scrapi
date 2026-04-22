---
title: Linter Configuration
description: Full reference for the cxaslint.yaml configuration file.
---

# Linter Configuration

The linter is configured by a `cxaslint.yaml` file in your project root. This file controls which directories the linter scans, how to override rule severities, per-rule options, file ignore patterns, and per-file rule overrides.

---

## Minimal configuration

A minimal `cxaslint.yaml` just points to your app directory:

```yaml
app_dir: cxas_app/My Support Agent
evals_dir: evals
```

Without a config file, the linter uses these defaults:

- `app_dir`: current directory
- `evals_dir`: `evals/` relative to the current directory
- All rules at their default severities
- No ignore patterns

---

## Full configuration reference

```yaml
# Path to your local app directory (produced by `cxas pull`)
app_dir: cxas_app/My Support Agent

# Path to your evaluations directory
evals_dir: evals

# Override rule severities
# Values: error, warning, info, off (or false to disable)
rules:
  I003: warning    # Keep IF/ELSE check as warning (default: warning)
  I007: off        # Disable instruction length check entirely
  T002: error      # Escalate missing docstring from warning to error

# Per-rule configuration options
options:
  I006:
    # Custom patterns for the hardcoded-data check
    patterns:
      - '\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'  # Phone numbers
      - '\$\d+(?:\.\d{2})?'               # Dollar amounts
      - '\b\d{5}(?:-\d{4})?\b'            # ZIP codes

  I007:
    # Maximum word count before I007 triggers
    max_words: 4000  # Default: 3000

# Glob patterns for files to ignore
ignore:
  - "agents/legacy-agent/**"
  - "tools/deprecated_tool/**"
  - "evals/archive/**"

# Per-file rule overrides
per_file:
  "agents/support-root/instruction.txt":
    I007: off  # This agent's instruction is intentionally long
    I003: info # Relax IF/ELSE check for this file only
  "tools/internal_tool/python_function/python_code.py":
    T005: off  # This tool intentionally uses high-cardinality args
```

---

## Field reference

### `app_dir`

**Type:** string  
**Default:** current directory

The path to your local app directory — the one created by `cxas pull`. The linter traverses this directory to find agents, tools, callbacks, and configuration files.

```yaml
app_dir: cxas_app/My Support Agent
```

You can use a relative or absolute path.

### `evals_dir`

**Type:** string  
**Default:** `evals/`

The path to your evaluations directory. The linter checks golden and simulation YAML files in this directory for issues.

```yaml
evals_dir: evals
```

### `rules`

**Type:** dict  
**Default:** all rules at their default severities

Override the severity of specific rules. Use the rule ID as the key and a severity string as the value.

```yaml
rules:
  I007: off        # Completely disable this rule
  T001: error      # Make this rule an error instead of a warning
  C006: warning    # Keep at warning (this is its default anyway)
```

Valid severity values: `error`, `warning`, `info`, `off` (or `false` to disable).

### `options`

**Type:** dict  
**Default:** empty

Per-rule configuration options. The available options depend on the rule. Currently, rules with configurable options are:

**I006 — hardcoded-data:**

```yaml
options:
  I006:
    patterns:
      - '\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'  # Custom phone number pattern
```

If `patterns` is specified, it *replaces* the default patterns (phone numbers and dollar amounts). To extend rather than replace, include the default patterns in your list.

**I007 — instruction-too-long:**

```yaml
options:
  I007:
    max_words: 4000  # Default is 3000
```

### `ignore`

**Type:** list of strings  
**Default:** empty

Glob patterns for files and directories to ignore. Patterns are matched against relative file paths within your project.

```yaml
ignore:
  - "agents/legacy-agent/**"      # Entire agent subtree
  - "**/*_deprecated*"            # Any file with "deprecated" in the name
  - "evals/archive/**/*.yaml"     # Specific subdirectory
```

Use `**` to match any number of path components, and `*` to match within a single path component.

### `per_file`

**Type:** dict  
**Default:** empty

Per-file rule overrides. The key is a relative file path (or glob pattern), and the value is a dict of rule ID to severity — the same format as the top-level `rules` block.

```yaml
per_file:
  "agents/support-root/instruction.txt":
    I007: off   # Don't check length for this specific file
    I003: info  # Relax IF/ELSE check

  "tools/*/python_function/python_code.py":
    T005: off   # Disable high-cardinality check for all tools
```

Per-file overrides take precedence over both the top-level `rules` and the rule's `default_severity`.

---

## Annotated complete example

Here's a realistic `cxaslint.yaml` for a production project:

```yaml
# Where your pulled app lives
app_dir: cxas_app/Customer Support Agent

# Where your eval files live
evals_dir: evals

# Rule severity overrides
rules:
  # We're strict about tool error handling
  T001: error

  # We allow longer instructions for complex agents
  I007: off

  # We track unused tools but don't block on them
  I012: info

  # Schema validation is important for us
  V001: error
  V002: error
  V003: error

# Per-rule options
options:
  I006:
    # Our agents deal with financial data, so we check for more patterns
    patterns:
      - '\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'   # US phone numbers
      - '\$\d+(?:\.\d{2})?'                 # Dollar amounts
      - '\b\d{4}[\s-]\d{4}[\s-]\d{4}[\s-]\d{4}\b'  # Credit card numbers

# Files to skip entirely
ignore:
  # Legacy agent being refactored — don't block the team on linting
  - "agents/v1-legacy/**"
  # Archived evals that we keep for reference only
  - "evals/archive/**"

# File-specific overrides
per_file:
  # The root agent is intentionally complex
  "agents/customer-service-root/instruction.txt":
    I003: info  # Don't warn on IF/ELSE — this complexity is intentional

  # Internal tooling doesn't need the full error handling pattern
  "tools/internal_debug_tool/**":
    T001: off
    T002: off
```

---

## Running with a custom config file

If you want to use a config file with a non-standard name or location:

```bash
cxas lint --config path/to/my-lint-config.yaml
```

This is useful when you have different lint configurations for different environments (e.g., a stricter config for CI than for local development).
