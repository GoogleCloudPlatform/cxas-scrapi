---
title: Submitting Pull Requests
---

# Submitting Pull Requests

We appreciate your contributions. This guide covers our PR process to help your changes land smoothly.

## Before You Start

1. **Check for existing issues** — Someone may already be working on the same thing. Look through [open issues](https://github.com/GoogleCloudPlatform/cxas-scrapi/issues) and [open PRs](https://github.com/GoogleCloudPlatform/cxas-scrapi/pulls) first.
2. **Open an issue for large changes** — If you're planning a significant feature or refactor, open an issue to discuss the approach before writing code. This saves everyone time.

## Creating Your PR

### 1. Create a feature branch

```bash
git checkout -b feature/your-feature-name
```

Use descriptive branch names:

- `feature/add-guardrail-evals` for new features
- `fix/lint-rule-i007-false-positive` for bug fixes
- `docs/improve-auth-guide` for documentation

### 2. Make your changes

- Keep commits focused — one logical change per commit.
- Write clear commit messages that explain *why*, not just *what*.
- Follow the [code style guide](code-style.md).

### 3. Run checks locally

Before pushing, make sure everything passes:

```bash
ruff check src/ tests/       # Lint check
ruff format --check src/ tests/  # Format check
pytest                        # Run tests
```

If you changed documentation:

```bash
mkdocs build --strict  # Catches broken links and warnings
```

### 4. Push and create the PR

```bash
git push origin feature/your-feature-name
```

Then open a pull request on GitHub. In your PR description:

- **Summarize what changed** — A brief explanation of the problem and your solution.
- **Link related issues** — Use "Fixes #123" or "Relates to #456".
- **Describe how to test** — Help reviewers verify your changes.

## What Makes a Good PR

- **Small and focused** — Smaller PRs are easier to review and less likely to introduce issues. If your change touches many files, consider splitting it into multiple PRs.
- **Tests included** — New features should come with tests. Bug fixes should include a test that would have caught the bug.
- **Documentation updated** — If your change affects the CLI, API, or user-facing behavior, update the relevant documentation.

## Review Process

1. A maintainer will review your PR, usually within a few business days.
2. You may receive feedback requesting changes — this is normal and collaborative, not adversarial.
3. Once approved, a maintainer will merge your PR.

## Tips

- If CI fails, check the logs carefully. Pre-commit hooks run `ruff` and `pytest` — the same checks you can run locally.
- Don't force-push to a PR branch after review has started. It makes it harder for reviewers to see what changed.
- If your PR has been open for a while without review, it's fine to leave a polite comment asking for attention.
