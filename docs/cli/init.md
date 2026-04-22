# cxas init

`cxas init` bootstraps your project with the AI agent development skills and configuration files you need to get the most out of CXAS SCRAPI — run it once at the start of a project and you're good to go.

## Usage

```
cxas init [--target-dir DIR] [--force]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--target-dir DIR` | No | `.` (current directory) | Directory to install the skill files into. |
| `--force` | No | `false` | Overwrite existing files without prompting. Without this flag, the CLI asks you for each file that already exists: overwrite, skip, overwrite all, or quit. |

## What Gets Installed

`cxas init` copies a set of bundled skill files from the package into your project directory. These typically include:

- **`.agents/`** — Agent skill definitions that teach AI coding assistants how to build, test, and deploy CXAS agents.
- **`.claude/`** — Claude-specific configuration for using the skills via Claude Code.
- **`.gemini/`** — Gemini-specific configuration.
- **`AGENTS.md`** — Top-level instructions for AI agents working in this repository.

The exact contents depend on the version of `cxas-scrapi` you have installed. You can inspect what was bundled at `{sys.prefix}/share/cxas-scrapi/skills/`.

## Interactive Overwrite Behaviour

When `--force` is not set and a file already exists, you're prompted:

```
  'AGENTS.md' already exists. [o]verwrite / [a]ll / [s]kip / [q]uit?
```

| Choice | Effect |
|--------|--------|
| `o` / `overwrite` | Overwrite just this file and continue prompting for others. |
| `a` / `all` | Overwrite this file and all remaining files without further prompting. |
| `s` / `skip` | Keep the existing file and move on. |
| `q` / `quit` | Abort immediately. Nothing further is installed. |

## Examples

**Initialize a brand-new project:**

```bash
cd my-new-agent-project
cxas init
```

**Re-initialize and force-overwrite all skill files (useful after upgrading `cxas-scrapi`):**

```bash
cxas init --force
```

**Install into a specific subdirectory:**

```bash
cxas init --target-dir ./infra/agent-scaffold
```

**Check what would be installed (by inspecting the bundled skills directory):**

```bash
ls "$(python -c 'import sys; print(sys.prefix)')/share/cxas-scrapi/skills/"
```

## Related Commands

- [`cxas lint`](lint.md) — Lint your app after bootstrapping to make sure everything is in order.
- [`cxas pull`](pull.md) — Download an existing app to edit locally.
- [`cxas init-github-action`](init-github-action.md) — Generate GitHub Actions CI workflows after initializing.
