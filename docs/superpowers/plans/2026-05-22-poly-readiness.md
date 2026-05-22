# Poly Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pre-launch `cxas poly readiness` report that shows whether a polymorphic SCRAPI project is ready for design-partner review and what gaps remain.

**Architecture:** Add a focused `cxas_scrapi.poly.readiness` module that composes existing validators, compiler, and diff reports without changing adapter-card schema or runtime behavior. Wire it into `cli/poly_cli.py` as text and JSON output, then document the workflow in the CLI and polymorphism guide.

**Tech Stack:** Python 3.10+, argparse CLI, dataclasses, existing `PolymorphismEngine`, existing AD validators, existing `build_diff_report`, pytest.

---

### Task 1: Report Builder Tests

**Files:**
- Create: `tests/cxas_scrapi/poly/test_readiness.py`
- Create: `src/cxas_scrapi/poly/readiness.py`

- [ ] **Step 1: Write failing tests for the report shape**

```python
from pathlib import Path

from cxas_scrapi.poly.engine import PolymorphismEngine
from cxas_scrapi.poly.readiness import build_readiness_report


def test_readiness_report_marks_clean_channel_ready(base_dir: Path):
    engine = PolymorphismEngine(str(base_dir))
    engine.load_base_project()
    engine.load_adapter_cards()

    report = build_readiness_report(engine, str(base_dir))

    chat = next(c for c in report["channels"] if c["channel"] == "chat")
    assert report["schema_version"] == "poly-readiness/v1"
    assert chat["status"] == "ready"
    assert chat["compiled"] is True
    assert chat["diff_summary"]["tools_added"] == 1
    assert chat["eval_coverage"]["evaluations"]["channel_count"] == 1
    assert chat["next_steps"] == [
        "Run cxas poly build, lint the compiled output, and run channel evals."
    ]


def test_readiness_report_flags_adapter_without_channel_evals(base_dir: Path):
    engine = PolymorphismEngine(str(base_dir))
    engine.load_base_project()
    engine.load_adapter_cards()

    report = build_readiness_report(engine, str(base_dir))

    voice = next(c for c in report["channels"] if c["channel"] == "voice")
    assert voice["status"] == "attention"
    assert any(issue["rule_id"] == "AD006" for issue in voice["issues"])
    assert any("channel-specific evaluations" in step for step in voice["next_steps"])
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run --with-editable . --with alive-progress pytest tests/cxas_scrapi/poly/test_readiness.py -q
```

Expected: import failure for `cxas_scrapi.poly.readiness`.

- [ ] **Step 3: Add minimal report builder**

Create `src/cxas_scrapi/poly/readiness.py` with:

```python
"""Readiness reports for polymorphic SCRAPI projects."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from cxas_scrapi.poly.diffing import build_diff_report
from cxas_scrapi.poly.engine import CompilationError, PolymorphismEngine
from cxas_scrapi.poly.validators import validate_adapter_card, validate_all_adapters

READINESS_SCHEMA_VERSION = "poly-readiness/v1"


def build_readiness_report(engine: PolymorphismEngine, app_dir: str) -> Dict[str, Any]:
    ...
```

The function must:
- Include `schema_version`, `app_dir`, `summary`, `adapter_errors`, and `channels`.
- Validate each adapter with existing AD rules.
- Add duplicate-channel AD007 issues from `validate_all_adapters`.
- Compile valid channels and include `build_diff_report(...)[summary]`.
- Mark channel `status` as `blocked` for errors, `attention` for warnings, and `ready` otherwise.
- Add concrete `next_steps` based on blocked/attention/ready state.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
uv run --with-editable . --with alive-progress pytest tests/cxas_scrapi/poly/test_readiness.py -q
```

Expected: 2 passed.

### Task 2: Coverage And Duplicate Eval Diagnostics

**Files:**
- Modify: `src/cxas_scrapi/poly/readiness.py`
- Modify: `tests/cxas_scrapi/poly/test_readiness.py`

- [ ] **Step 1: Write failing duplicate-name test**

```python
import shutil


def test_readiness_report_warns_when_channel_eval_reuses_base_name(copied_base: Path):
    base_eval = copied_base / "evaluations" / "Chat_Test_Eval"
    base_eval.mkdir(parents=True)
    shutil.copy2(
        copied_base
        / "adapters"
        / "chat_evals"
        / "Chat_Test_Eval"
        / "Chat_Test_Eval.yaml",
        base_eval / "Chat_Test_Eval.yaml",
    )

    engine = PolymorphismEngine(str(copied_base))
    engine.load_base_project()
    engine.load_adapter_cards()

    report = build_readiness_report(engine, str(copied_base))

    chat = next(c for c in report["channels"] if c["channel"] == "chat")
    assert chat["status"] == "attention"
    assert chat["eval_coverage"]["evaluations"]["duplicate_names"] == [
        "Chat_Test_Eval"
    ]
    assert any("duplicate evaluation names" in step for step in chat["next_steps"])
```

- [ ] **Step 2: Run duplicate test to verify RED**

Run:

```bash
uv run --with-editable . --with alive-progress pytest tests/cxas_scrapi/poly/test_readiness.py::test_readiness_report_warns_when_channel_eval_reuses_base_name -q
```

Expected: failure because duplicate names are not reported yet.

- [ ] **Step 3: Implement eval namespace checks**

Add helpers in `readiness.py`:

```python
def _item_names(root: Path, subdir: str) -> List[str]:
    path = root / subdir
    if not path.is_dir():
        return []
    names: List[str] = []
    for child in sorted(path.iterdir()):
        if child.is_dir():
            names.append(child.name)
        elif child.suffix in (".json", ".yaml", ".yml"):
            names.append(child.stem)
    return names
```

Use it to populate coverage for `evaluations`, `evaluationExpectations`, and `evaluationDatasets` with `base_count`, `channel_count`, and `duplicate_names`.

- [ ] **Step 4: Run readiness tests**

Run:

```bash
uv run --with-editable . --with alive-progress pytest tests/cxas_scrapi/poly/test_readiness.py -q
```

Expected: all readiness tests pass.

### Task 3: CLI Wiring

**Files:**
- Modify: `src/cxas_scrapi/cli/poly_cli.py`
- Modify: `tests/cxas_scrapi/poly/test_poly_cli.py`

- [ ] **Step 1: Write failing CLI JSON test**

```python
def test_cli_readiness_json(base_dir: Path, capsys):
    code, out, _err = _run_poly(
        ["readiness", "--app-dir", str(base_dir), "--format", "json"],
        capsys,
    )

    assert code == 0
    report = json.loads(out)
    assert report["schema_version"] == "poly-readiness/v1"
    assert any(c["channel"] == "chat" for c in report["channels"])
```

- [ ] **Step 2: Run CLI test to verify RED**

Run:

```bash
uv run --with-editable . --with alive-progress pytest tests/cxas_scrapi/poly/test_poly_cli.py::test_cli_readiness_json -q
```

Expected: argparse error because `readiness` is not registered.

- [ ] **Step 3: Add CLI handler and registration**

Add imports:

```python
from cxas_scrapi.poly.readiness import build_readiness_report
```

Add `poly_readiness(args)` that loads the engine, builds the report, prints JSON for `--format json`, renders concise text otherwise, and exits non-zero on errors or on warnings when `--strict` is passed.

Register:

```python
p_readiness = poly_subparsers.add_parser(
    "readiness",
    help="Summarize adapter validation, diffs, eval coverage, and build readiness.",
)
p_readiness.add_argument("--app-dir", default=".", help="Path to the agent project root.")
p_readiness.add_argument("--format", choices=("text", "json"), default="text")
p_readiness.add_argument("--strict", action="store_true")
p_readiness.set_defaults(func=poly_readiness)
```

- [ ] **Step 4: Run CLI test to verify GREEN**

Run:

```bash
uv run --with-editable . --with alive-progress pytest tests/cxas_scrapi/poly/test_poly_cli.py::test_cli_readiness_json -q
```

Expected: 1 passed.

### Task 4: Docs And Verification

**Files:**
- Modify: `docs/cli/poly.md`
- Modify: `docs/guides/polymorphism.md`
- Modify: `README.md` if the top-level workflow list needs the new command.
- Modify: `PLAN.md`, `findings.md`, `progress.md`

- [ ] **Step 1: Document `cxas poly readiness`**

Add it to the command table, include usage, options, text/JSON purpose, exit codes, and where it fits: after `validate/diff` while preparing a launch/design-partner review.

- [ ] **Step 2: Update the polymorphism guide workflow**

Add a short readiness step after `diff`, before `build`, explaining that it catches validation blockers, warning-level coverage gaps, duplicate eval names, and compileability without writing output.

- [ ] **Step 3: Run targeted checks**

Run:

```bash
git diff --check
uv run --with-editable . --with alive-progress ruff check src/cxas_scrapi/poly src/cxas_scrapi/cli/poly_cli.py tests/cxas_scrapi/poly
uv run --with-editable . --with alive-progress pytest tests/cxas_scrapi/poly -q
uv run --with-editable . --with alive-progress cxas poly readiness --app-dir examples/bella_notte
uv run --with-editable . --with alive-progress cxas poly readiness --app-dir examples/bella_notte --format json
```

Expected: checks pass; readiness exits 0 for Bella Notte.
