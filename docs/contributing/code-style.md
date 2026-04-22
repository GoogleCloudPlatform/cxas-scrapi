---
title: Code Style
---

# Code Style

Consistent code style makes the codebase easier to read and maintain. We use automated tools to handle most formatting decisions, so you can focus on writing good code rather than debating style.

## Linting with Ruff

We use [Ruff](https://docs.astral.sh/ruff/) for both linting and formatting. The configuration lives in `pyproject.toml`:

- **Line length**: 80 characters
- **Target version**: Python 3.10
- **Enabled rule sets**: pyflakes (`F`), pycodestyle (`E`), McCabe complexity (`C90`)

Ruff runs automatically via pre-commit hooks on every commit. You can also run it manually:

```bash
ruff check src/ tests/        # Check for lint issues
ruff check src/ tests/ --fix  # Auto-fix what's possible
ruff format src/ tests/       # Format code
```

## Docstrings

We follow [Google-style docstrings](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings). This is important because our API reference documentation is auto-generated from docstrings using `mkdocstrings`.

Here's what a well-written docstring looks like:

```python
def get_apps_map(
    self, project_id: str = None, location: str = None
) -> Dict[str, str]:
    """Returns a mapping of app display names to resource names.

    This is a convenience method that fetches all apps and returns
    them as a dictionary, making it easy to look up an app's
    resource name by its human-readable display name.

    Args:
        project_id: The GCP project ID. Uses the instance default
            if not provided.
        location: The GCP location (e.g., "us", "global"). Uses the
            instance default if not provided.

    Returns:
        A dictionary mapping display names to full resource names.
        For example: {"My App": "projects/my-proj/locations/us/apps/abc123"}

    Raises:
        PermissionError: If the caller lacks the required IAM role.
    """
```

Key points:

- **First line**: A concise summary of what the method does, in imperative mood ("Returns..." not "Return...").
- **Extended description**: Optional. Explain *why* someone would use this method, not just *what* it does.
- **Args**: Document each parameter with its type and purpose.
- **Returns**: Describe the return value with an example when it helps.
- **Raises**: List exceptions that callers should handle.

## Type Hints

Use type hints on all public method signatures. They serve as documentation and help IDEs provide better autocompletion:

```python
def create_app(
    self,
    display_name: str,
    description: str = "",
    project_id: str = None,
    location: str = None,
) -> Any:
```

## Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Classes | PascalCase | `ToolEvals`, `SimulationEvals` |
| Methods | snake_case | `get_apps_map`, `run_tool_tests` |
| Constants | UPPER_SNAKE | `GLOBAL_SCOPES`, `SAMPLE_RATE` |
| Private methods | Leading underscore | `_parse_response` |
| Test files | `test_` prefix | `test_linter.py` |

## Testing

- Write tests for any new functionality.
- Tests live in `tests/` and mirror the `src/` directory structure.
- Use `pytest` conventions (functions prefixed with `test_`).
- Aim for clear, readable test names that describe the scenario being tested.

```python
def test_lint_detects_missing_xml_structure():
    """I001 should flag instructions without <role> tags."""
    ...
```
