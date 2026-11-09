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

"""Adversarial regression tests for `python_bundler.py`.

Covers:
- Round-trip invariants: f-strings with nested expressions, raw triple quotes,
  embedded marker strings/comments, empty shared modules, and swapped digest
  headers across entrypoints.
- Regressions:
  - `strip_shared_bundles(..., local_source_dir=...)` detects local drift.
  - Non-`exec` tampering and empty bundle blocks inside
    `# === BEGIN CXAS_SHARED_BUNDLE ... ===` are rejected.
  - `from python_lib import submod` and relative `from . import sibling`
    imports are bundled.
  - Triple newlines inside user string literals survive bundle + strip.
  - Shared modules with a `SyntaxError` fail at bundle time.
  - A second tampered bundle block or a comment-only Console edit is rejected.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from typing import TYPE_CHECKING

import pytest

from cxas_scrapi.core.python_bundler import (
    BundleDigestMismatchError,
    PythonBundlerError,
    bundle_shared_python,
    strip_shared_bundles,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_entrypoint(app_dir: Path, name: str, source: str) -> Path:
    cb_dir = app_dir / "agents" / "root_agent" / "before_model_callbacks" / name
    cb_dir.mkdir(parents=True, exist_ok=True)
    ep = cb_dir / "python_code.py"
    ep.write_text(source, encoding="utf-8")
    return ep


# ---------------------------------------------------------------------------
# Round-trip invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case_id", "mod_src"),
    [
        (
            "fstrings_and_raw_triple_quotes",
            (
                'RAW_BLOCK = r"""Line 1\\n", "exec"\\n# === END CXAS_SHARED_BUNDLE ==="""\n'
                "TAG = 'brand_b'\n"
                'FSTR = f"Brand={TAG!r} braces={{1, 2}} nested={TAG.upper()}"\n'
                "VALUE = RAW_BLOCK + '|' + FSTR\n"
            ),
        ),
        (
            "empty_shared_module",
            "",
        ),
        (
            "marker_and_exec_inside_comments_and_docstrings",
            (
                '"""\n'
                "# === BEGIN CXAS_SHARED_BUNDLE (sha256:0000000000000000) ===\n"
                'exec(compile("1/0", "python_lib/fake.py", "exec"), _cxas_mod.__dict__)\n'
                "# === END CXAS_SHARED_BUNDLE ===\n"
                '"""\n'
                "# # === END CXAS_SHARED_BUNDLE ===\n"
                "VALUE = 'safe'\n"
            ),
        ),
    ],
)
def test_qa_pass_ast_roundtrip_tricky_valid_modules(
    tmp_path: Path, case_id: str, mod_src: str
) -> None:
    """Valid tricky Python modules (f-strings, raw triple quotes, empty module) round-trip cleanly."""
    app_dir = tmp_path / case_id
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "__init__.py").write_text("", encoding="utf-8")
    (plib / "edge.py").write_text(mod_src, encoding="utf-8")

    ep_src = (
        "import python_lib.edge\n"
        "def cb(ctx):\n"
        "    return getattr(python_lib.edge, 'VALUE', 'EMPTY')\n"
    )
    ep = _make_entrypoint(app_dir, "cb_01", ep_src)

    bundle_shared_python(app_dir)
    bundled = ep.read_text(encoding="utf-8")
    assert "# === BEGIN CXAS_SHARED_BUNDLE (sha256:" in bundled

    stripped = strip_shared_bundles(app_dir)
    assert stripped == [ep]
    assert ep.read_text(encoding="utf-8").strip() == ep_src.strip()


def test_qa_pass_swapped_digest_between_entrypoints_raises(
    tmp_path: Path,
) -> None:
    """Swapping a valid 16-hex header digest from another module bundle raises BundleDigestMismatchError."""
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "mod_a.py").write_text("A = 1\n", encoding="utf-8")
    (plib / "mod_b.py").write_text("B = 2\n", encoding="utf-8")

    ep_a = _make_entrypoint(app_dir, "cb_a", "import python_lib.mod_a\n")
    ep_b = _make_entrypoint(app_dir, "cb_b", "import python_lib.mod_b\n")

    bundle_shared_python(app_dir)
    text_a = ep_a.read_text(encoding="utf-8")
    text_b = ep_b.read_text(encoding="utf-8")

    header_a = text_a.splitlines()[0]
    header_b = text_b.splitlines()[0]
    assert header_a != header_b

    ep_a.write_text(text_a.replace(header_a, header_b, 1), encoding="utf-8")
    with pytest.raises(BundleDigestMismatchError):
        strip_shared_bundles(app_dir)


# ---------------------------------------------------------------------------
# Regressions
# ---------------------------------------------------------------------------


def test_qa04_strip_shared_bundles_detects_local_source_dir_drift(
    tmp_path: Path,
) -> None:
    """`local_source_dir` drift check must raise when local `python_lib/*.py` diverged."""
    pulled_dir = tmp_path / "pulled_app"
    local_dir = tmp_path / "local_repo"

    for root in (pulled_dir, local_dir):
        plib = root / "python_lib"
        plib.mkdir(parents=True)
        (plib / "__init__.py").write_text("", encoding="utf-8")
        (plib / "helper.py").write_text("BRAND = 'brand_b'\n", encoding="utf-8")

    _make_entrypoint(
        pulled_dir,
        "cb_01",
        "import python_lib.helper\ndef cb():\n    return python_lib.helper.BRAND\n",
    )
    bundle_shared_python(pulled_dir)
    shutil.rmtree(pulled_dir / "python_lib")

    # Simulate local developer edit to python_lib/helper.py before pulling from Console.
    (local_dir / "python_lib" / "helper.py").write_text(
        "BRAND = 'brand_c_edited_locally'\n", encoding="utf-8"
    )

    with pytest.raises(BundleDigestMismatchError, match="Drift"):
        strip_shared_bundles(pulled_dir, local_source_dir=local_dir)


def test_qa05_strip_shared_bundles_rejects_non_exec_console_tampering(
    tmp_path: Path,
) -> None:
    """Console statements added inside bundle markers must raise BundleDigestMismatchError."""
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "helper.py").write_text("BRAND = 'brand_b'\n", encoding="utf-8")
    ep = _make_entrypoint(app_dir, "cb_01", "import python_lib.helper\n")
    bundle_shared_python(app_dir)

    bundled = ep.read_text(encoding="utf-8")
    tampered = bundled.replace(
        "# === END CXAS_SHARED_BUNDLE ===",
        '_cxas_sys.modules["python_lib.helper"].BRAND = "brand_a_console_hotfix"\n'
        "# === END CXAS_SHARED_BUNDLE ===",
        1,
    )
    ep.write_text(tampered, encoding="utf-8")

    with pytest.raises(BundleDigestMismatchError):
        strip_shared_bundles(app_dir)


def test_qa05_strip_shared_bundles_rejects_empty_bundle_block(
    tmp_path: Path,
) -> None:
    """A bundle marker block with zero bundled modules and arbitrary code must be rejected."""
    app_dir = tmp_path / "app"
    empty_digest = hashlib.sha256(b"").hexdigest()[:16]  # e3b0c44298fc1c14
    forged = (
        f"# === BEGIN CXAS_SHARED_BUNDLE (sha256:{empty_digest}) ===\n"
        "CONSOLE_HOTFIX = 'important_business_logic'\n"
        "# === END CXAS_SHARED_BUNDLE ===\n"
        "def cb():\n"
        "    return 1\n"
    )
    _make_entrypoint(app_dir, "cb_01", forged)

    with pytest.raises(BundleDigestMismatchError):
        strip_shared_bundles(app_dir)


def test_qa06_bundle_shared_python_resolves_from_package_import_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`from python_lib import helper` must bundle `python_lib.helper`."""
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "__init__.py").write_text("", encoding="utf-8")
    (plib / "helper.py").write_text("ANSWER = 42\n", encoding="utf-8")

    ep = _make_entrypoint(
        app_dir,
        "cb_01",
        "from python_lib import helper\ndef run():\n    return helper.ANSWER\n",
    )
    bundle_shared_python(app_dir)
    shutil.rmtree(plib)

    monkeypatch.delitem(sys.modules, "python_lib", raising=False)
    monkeypatch.delitem(sys.modules, "python_lib.helper", raising=False)
    ns: dict[str, object] = {}
    exec(ep.read_text(encoding="utf-8"), ns)
    assert ns["run"]() == 42  # type: ignore[operator]


def test_qa06_bundle_shared_python_resolves_relative_from_dot_import_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`from . import sibling` inside a `python_lib` package must bundle `sibling`."""
    app_dir = tmp_path / "app"
    pkg = app_dir / "python_lib" / "pkg"
    pkg.mkdir(parents=True)
    (app_dir / "python_lib" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sibling.py").write_text("TOKEN = 'brand_b'\n", encoding="utf-8")
    (pkg / "consumer.py").write_text(
        "from . import sibling\nVAL = sibling.TOKEN\n", encoding="utf-8"
    )

    ep = _make_entrypoint(
        app_dir,
        "cb_01",
        "import python_lib.pkg.consumer\ndef run():\n    return python_lib.pkg.consumer.VAL\n",
    )
    bundle_shared_python(app_dir)
    shutil.rmtree(app_dir / "python_lib")

    for k in [
        m
        for m in sys.modules
        if m == "python_lib" or m.startswith("python_lib.")
    ]:
        monkeypatch.delitem(sys.modules, k, raising=False)
    ns: dict[str, object] = {}
    exec(ep.read_text(encoding="utf-8"), ns)
    assert ns["run"]() == "brand_b"  # type: ignore[operator]


def test_qa12_bundle_strip_roundtrip_preserves_multiline_string_triple_newlines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bundling + stripping must not mutate `\\n\\n\\n` inside user string literals."""
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "helper.py").write_text("X = 1\n", encoding="utf-8")

    original = (
        "import python_lib.helper\n"
        'LEGAL_TEXT = """Paragraph 1\n\n\n\nParagraph 2"""\n'
        "def run():\n"
        "    return LEGAL_TEXT\n"
    )
    ep = _make_entrypoint(app_dir, "cb_01", original)

    bundle_shared_python(app_dir)

    # The bundled entrypoint is self-contained, so it runs without python_lib
    # on sys.path; drop any module a previous test left behind.
    for k in [m for m in sys.modules if m.split(".")[0] == "python_lib"]:
        monkeypatch.delitem(sys.modules, k, raising=False)
    ns: dict[str, object] = {}
    exec(ep.read_text(encoding="utf-8"), ns)
    assert ns["run"]() == "Paragraph 1\n\n\n\nParagraph 2"  # type: ignore[operator]

    strip_shared_bundles(app_dir)
    assert ep.read_text(encoding="utf-8") == original


def test_qa13_bundle_shared_python_rejects_syntax_error_in_shared_module(
    tmp_path: Path,
) -> None:
    """`bundle_shared_python` must fail fast on `SyntaxError` in `python_lib/*.py`."""
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "broken.py").write_text(
        "def broken(:\n    pass\n", encoding="utf-8"
    )
    _make_entrypoint(app_dir, "cb_01", "import python_lib.broken\n")

    with pytest.raises((SyntaxError, PythonBundlerError)):
        bundle_shared_python(app_dir)


def test_qa18_strip_shared_bundles_rejects_second_tampered_bundle_block_and_comment_hotfix(
    tmp_path: Path,
) -> None:
    """`strip_shared_bundles` must not leave a tampered second bundle block or drop Console comments."""
    app_dir = tmp_path / "app_multi"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "helper.py").write_text("X = 1\n", encoding="utf-8")
    ep = _make_entrypoint(app_dir, "cb_01", "import python_lib.helper\n")
    bundle_shared_python(app_dir)

    bundled = ep.read_text(encoding="utf-8")
    end_marker = "# === END CXAS_SHARED_BUNDLE ==="
    valid_block = bundled[: bundled.index(end_marker) + len(end_marker)]
    tampered_block = valid_block.replace("X = 1", "X = 999")
    ep.write_text(
        valid_block + "\n" + tampered_block + "\nimport python_lib.helper\n",
        encoding="utf-8",
    )

    with pytest.raises(BundleDigestMismatchError):
        strip_shared_bundles(app_dir)

    # Second check: comment-only Console annotation inside bundle markers should raise per byte-for-byte docstring.
    app_comment = tmp_path / "app_comment"
    plib_c = app_comment / "python_lib"
    plib_c.mkdir(parents=True)
    (plib_c / "helper.py").write_text("X = 1\n", encoding="utf-8")
    ep_c = _make_entrypoint(app_comment, "cb_01", "import python_lib.helper\n")
    bundle_shared_python(app_comment)
    ep_c.write_text(
        ep_c.read_text(encoding="utf-8").replace(
            end_marker,
            "# CONSOLE HOTFIX NOTE: keep until ticket-1234\n" + end_marker,
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(BundleDigestMismatchError):
        strip_shared_bundles(app_comment)
