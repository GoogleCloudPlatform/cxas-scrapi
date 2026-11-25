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

"""Round-trip and tamper tests for `bundle_shared_python` / `strip_shared_bundles`.

Regression coverage for the former regex-based bundle parser, which mis-split
bundled module sources containing `", "` (and other quote sequences) and then
raised a false `BundleDigestMismatchError` on an untampered bundle.
"""

import re
import shutil
import sys
import types
from pathlib import Path

import pytest

from cxas_scrapi.core.python_bundler import (
    BundleDigestMismatchError,
    PythonBundlerError,
    bundle_shared_python,
    strip_shared_bundles,
)

ENTRYPOINT_SRC = (
    "import python_lib.tricky\n"
    "def before_model_callback(callback_context, llm_request):\n"
    "    return python_lib.tricky.VALUE\n"
)

TRICKY_SOURCES = {
    "comma_quote_dict_and_call": (
        'CFG = {"a": "x", "b": "y"}\n'
        "def foo(a, b):\n"
        "    return a + b\n"
        'VALUE = foo("a", "b") + CFG["a"]\n'
    ),
    "single_and_double_quotes": (
        "S1 = 'it\\'s'\n"
        'S2 = "say \\"hi\\""\n'
        "S3 = '\", \"'\n"
        "S4 = \"', '\"\n"
        "VALUE = S1 + S2 + S3 + S4\n"
    ),
    "triple_quoted_docstrings": (
        '"""Module doc with "quotes", \'single\' and ", " inside."""\n'
        "MULTI = '''line one\n"
        "line \"two\", 'three'\n"
        "'''\n"
        "def f():\n"
        '    """Doc: ", " and \\"escaped\\"."""\n'
        "    return MULTI\n"
        "VALUE = f()\n"
    ),
    "backslashes_and_regexes": (
        "import re\n"
        'PAT = re.compile(r"\\b(?:a|b)\\s+\\d{2}\\\\", re.IGNORECASE)\n'
        'WIN = "C:\\\\temp\\\\new\\tfile"\n'
        "RAW = r'\\n is not a newline, \", \" either'\n"
        "VALUE = PAT.pattern + WIN + RAW\n"
    ),
    "unicode": (
        'GREETING = "Grüße – 日本語 🙂, «guillemets», “curly”"\n'
        "VALUE = GREETING\n"
    ),
    "multiline_json_literal": (
        "TABLE = {\n"
        '    "brand_a": {"display_name": "Brand A", "urls": ["a", "b"]},\n'
        '    "brand_b": {"display_name": "brand_b", "urls": []},\n'
        "}\n"
        'VALUE = TABLE["brand_a"]["display_name"]\n'
    ),
    "embedded_bundle_lookalike": (
        '# exec(compile(\'x\', "python_lib/evil.py", "exec"), '
        "_cxas_mod.__dict__)\n"
        'TEXT = \'exec(compile("y", "python_lib/evil.py", "exec"), '
        "_cxas_mod.__dict__)'\n"
        "VALUE = TEXT\n"
    ),
}


def _make_app(tmp_path: Path, module_src: str) -> tuple[Path, Path]:
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "__init__.py").write_text('"""Shared lib."""\n', encoding="utf-8")
    (plib / "tricky.py").write_text(module_src, encoding="utf-8")
    cb_dir = app_dir / "agents" / "a" / "before_model_callbacks" / "cb_01"
    cb_dir.mkdir(parents=True)
    ep = cb_dir / "python_code.py"
    ep.write_text(ENTRYPOINT_SRC, encoding="utf-8")
    return app_dir, ep


def _exec_bundled(content: str) -> object:
    class FakeSys:
        modules: dict[str, types.ModuleType] = {}

    class FakeTypes:
        ModuleType = types.ModuleType

    scope = {"_cxas_sys": FakeSys, "_cxas_types": FakeTypes}
    exec(content, scope)
    return scope["before_model_callback"](None, None)


@pytest.mark.parametrize(
    "module_src", TRICKY_SOURCES.values(), ids=TRICKY_SOURCES.keys()
)
def test_strip_round_trip_with_tricky_module_sources(
    tmp_path: Path, module_src: str
) -> None:
    app_dir, ep = _make_app(tmp_path, module_src)
    expected_value = {}
    exec(module_src, expected_value)

    bundle_shared_python(app_dir)
    bundled = ep.read_text(encoding="utf-8")
    assert "# === BEGIN CXAS_SHARED_BUNDLE (sha256:" in bundled
    assert _exec_bundled(bundled) == expected_value["VALUE"]

    stripped = strip_shared_bundles(app_dir)

    assert stripped == [ep]
    content = ep.read_text(encoding="utf-8")
    assert "CXAS_SHARED_BUNDLE" not in content
    assert content.strip() == ENTRYPOINT_SRC.strip()


def test_strip_round_trip_all_tricky_modules_in_one_bundle(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    names = sorted(TRICKY_SOURCES)
    for name in names:
        (plib / f"{name}.py").write_text(TRICKY_SOURCES[name], encoding="utf-8")
    tool_dir = app_dir / "tools" / "t" / "python_function"
    tool_dir.mkdir(parents=True)
    ep = tool_dir / "python_code.py"
    orig = "".join(f"import python_lib.{n}\n" for n in names) + (
        "def t() -> str:\n    return 'ok'\n"
    )
    ep.write_text(orig, encoding="utf-8")

    bundle_shared_python(app_dir)
    bundled = ep.read_text(encoding="utf-8")
    assert len(re.findall(r"^    exec\(compile\(", bundled, re.M)) == len(names)

    assert strip_shared_bundles(app_dir) == [ep]
    assert ep.read_text(encoding="utf-8").strip() == orig.strip()


def _bundled_app(tmp_path: Path) -> tuple[Path, Path, str]:
    app_dir, ep = _make_app(
        tmp_path, TRICKY_SOURCES["comma_quote_dict_and_call"]
    )
    bundle_shared_python(app_dir)
    return app_dir, ep, ep.read_text(encoding="utf-8")


def test_tampered_module_source_raises(tmp_path: Path) -> None:
    app_dir, ep, bundled = _bundled_app(tmp_path)
    tampered = bundled.replace('"a": "x"', '"a": "HACKED"', 1)
    assert tampered != bundled
    ep.write_text(tampered, encoding="utf-8")

    with pytest.raises(BundleDigestMismatchError, match="tampered"):
        strip_shared_bundles(app_dir)
    assert ep.read_text(encoding="utf-8") == tampered


@pytest.mark.parametrize(
    ("pattern", "replacement"),
    [
        # Non-constant module source.
        (r"exec\(compile\(", "exec(compile(SRC_OVERRIDE or "),
        # Non-constant filename.
        (r'"python_lib/tricky\.py"', "FILENAME_VAR"),
        # Wrong compile mode.
        (r'"python_lib/tricky\.py", "exec"', '"python_lib/tricky.py", "eval"'),
        # Different target namespace.
        (r"_cxas_mod\.__dict__\)", "globals())"),
        # Injected exec with an unexpected shape.
        (
            r"(# === END CXAS_SHARED_BUNDLE ===)",
            r"    exec(open('x').read())\n\1",
        ),
        # Duplicate module registration.
        (
            r"(    exec\(compile\(.*?python_lib/tricky\.py.*?\n)",
            r"\1\1",
        ),
        # Block no longer parses.
        (r"(_CXAS_DIGEST = )", r"\1(("),
    ],
    ids=[
        "non_constant_source",
        "non_constant_filename",
        "wrong_mode",
        "wrong_namespace",
        "injected_exec",
        "duplicate_module",
        "unparsable_block",
    ],
)
def test_malformed_bundle_raises(
    tmp_path: Path, pattern: str, replacement: str
) -> None:
    app_dir, ep, bundled = _bundled_app(tmp_path)
    tampered, n = re.subn(pattern, replacement, bundled, count=1, flags=re.S)
    assert n == 1, pattern
    ep.write_text(tampered, encoding="utf-8")

    with pytest.raises(BundleDigestMismatchError):
        strip_shared_bundles(app_dir)
    assert ep.read_text(encoding="utf-8") == tampered


def test_force_strip_ignores_tampering(tmp_path: Path) -> None:
    app_dir, ep, bundled = _bundled_app(tmp_path)
    ep.write_text(
        bundled.replace("exec(compile(", "exec(compile(X or ", 1),
        encoding="utf-8",
    )

    assert strip_shared_bundles(app_dir, force=True) == [ep]
    assert ep.read_text(encoding="utf-8").strip() == ENTRYPOINT_SRC.strip()


LOSSLESS_ENTRYPOINTS = {
    "plain": ENTRYPOINT_SRC,
    "no_trailing_newline": ENTRYPOINT_SRC.rstrip("\n"),
    "blank_lines_and_multiline_string": (
        'import python_lib.tricky\n\n\n\nTEXT = """a\n\n\n\nb"""\n\n\n'
    ),
    "docstring_then_future": (
        '"""Doc."""\nfrom __future__ import annotations\n'
        "import python_lib.tricky\n"
    ),
}


@pytest.mark.parametrize(
    "entrypoint_src",
    LOSSLESS_ENTRYPOINTS.values(),
    ids=LOSSLESS_ENTRYPOINTS.keys(),
)
def test_bundle_then_strip_is_byte_for_byte_lossless(
    tmp_path: Path, entrypoint_src: str
) -> None:
    app_dir, ep = _make_app(tmp_path, TRICKY_SOURCES["unicode"])
    ep.write_text(entrypoint_src, encoding="utf-8")

    bundle_shared_python(app_dir)
    bundled = ep.read_text(encoding="utf-8")
    assert "CXAS_SHARED_BUNDLE" in bundled
    compile(bundled, str(ep), "exec")

    assert strip_shared_bundles(app_dir) == [ep]
    assert ep.read_text(encoding="utf-8") == entrypoint_src


def test_local_source_dir_drift_detected(tmp_path: Path) -> None:
    app_dir, ep, _ = _bundled_app(tmp_path)
    local = tmp_path / "local"
    shutil.copytree(app_dir / "python_lib", local / "python_lib")
    (local / "python_lib" / "tricky.py").write_text(
        "VALUE = 'drifted'\n", encoding="utf-8"
    )

    with pytest.raises(BundleDigestMismatchError, match="Drift"):
        strip_shared_bundles(app_dir, local_source_dir=local)
    assert "CXAS_SHARED_BUNDLE" in ep.read_text(encoding="utf-8")


def test_local_package_init_drift_detected(tmp_path: Path) -> None:
    app_dir, _ep, _ = _bundled_app(tmp_path)
    (app_dir / "python_lib" / "__init__.py").write_text(
        "EDITED = True\n", encoding="utf-8"
    )

    with pytest.raises(BundleDigestMismatchError, match="Drift"):
        strip_shared_bundles(app_dir)


def test_extra_statement_inside_guard_raises(tmp_path: Path) -> None:
    app_dir, ep, bundled = _bundled_app(tmp_path)
    tampered = bundled.replace(
        "    _cxas_sys.modules[",
        "    HOTFIX = 1\n    _cxas_sys.modules[",
        1,
    )
    assert tampered != bundled
    ep.write_text(tampered, encoding="utf-8")

    with pytest.raises(BundleDigestMismatchError, match="unexpected"):
        strip_shared_bundles(app_dir)
    assert ep.read_text(encoding="utf-8") == tampered


def test_shared_module_syntax_error_fails_at_bundle_time(
    tmp_path: Path,
) -> None:
    app_dir, ep = _make_app(tmp_path, "def broken(:\n    pass\n")

    with pytest.raises(PythonBundlerError, match="not valid Python"):
        bundle_shared_python(app_dir)
    assert ep.read_text(encoding="utf-8") == ENTRYPOINT_SRC


def test_from_package_and_relative_imports_are_bundled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir = tmp_path / "app"
    pkg = app_dir / "python_lib" / "pkg"
    pkg.mkdir(parents=True)
    (app_dir / "python_lib" / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "python_lib" / "helper.py").write_text(
        "H = 'h'\n", encoding="utf-8"
    )
    (pkg / "__init__.py").write_text(
        "from . import sibling\nfrom .. import helper\n", encoding="utf-8"
    )
    (pkg / "sibling.py").write_text("S = 's'\n", encoding="utf-8")
    tool_dir = app_dir / "tools" / "t" / "python_function"
    tool_dir.mkdir(parents=True)
    ep = tool_dir / "python_code.py"
    ep.write_text(
        "from python_lib import pkg\n"
        "def t():\n    return pkg.sibling.S + pkg.helper.H\n",
        encoding="utf-8",
    )

    bundle_shared_python(app_dir)
    bundled = ep.read_text(encoding="utf-8")
    for filename in (
        "python_lib/__init__.py",
        "python_lib/helper.py",
        "python_lib/pkg/__init__.py",
        "python_lib/pkg/sibling.py",
    ):
        assert f'"{filename}", "exec"' in bundled

    # Runs exactly as in CES: no python_lib/ on disk, fresh module registry.
    shutil.rmtree(app_dir / "python_lib")
    for name in [m for m in sys.modules if m.split(".")[0] == "python_lib"]:
        monkeypatch.delitem(sys.modules, name)
    ns: dict[str, object] = {}
    exec(bundled, ns)
    assert ns["t"]() == "sh"  # type: ignore[operator]

    assert strip_shared_bundles(app_dir) == [ep]


def _first_block(bundled: str) -> str:
    end = "# === END CXAS_SHARED_BUNDLE ===\n"
    return bundled[: bundled.index(end) + len(end)]


def test_second_bundle_block_raises_and_leaves_file(tmp_path: Path) -> None:
    app_dir, ep, bundled = _bundled_app(tmp_path)
    block = _first_block(bundled)
    doubled = block + bundled
    ep.write_text(doubled, encoding="utf-8")

    with pytest.raises(BundleDigestMismatchError, match="2 shared bundle"):
        strip_shared_bundles(app_dir)
    assert ep.read_text(encoding="utf-8") == doubled


def test_force_strips_every_bundle_block(tmp_path: Path) -> None:
    app_dir, ep, bundled = _bundled_app(tmp_path)
    block = _first_block(bundled)
    ep.write_text(block + block.replace('"x"', '"tampered"') + bundled)

    assert strip_shared_bundles(app_dir, force=True) == [ep]
    assert ep.read_text(encoding="utf-8") == ENTRYPOINT_SRC


@pytest.mark.parametrize(
    "marker",
    ["# === END CXAS_SHARED_BUNDLE ===", "# === BEGIN CXAS_SHARED_BUNDLE"],
)
def test_stray_marker_outside_block_raises(tmp_path: Path, marker: str) -> None:
    app_dir, ep, bundled = _bundled_app(tmp_path)
    tampered = bundled + marker + "\n"
    ep.write_text(tampered, encoding="utf-8")

    with pytest.raises(BundleDigestMismatchError, match="stray"):
        strip_shared_bundles(app_dir)
    assert ep.read_text(encoding="utf-8") == tampered


def test_unterminated_block_raises(tmp_path: Path) -> None:
    app_dir, ep, bundled = _bundled_app(tmp_path)
    tampered = bundled.replace("# === END CXAS_SHARED_BUNDLE ===\n", "", 1)
    ep.write_text(tampered, encoding="utf-8")

    with pytest.raises(BundleDigestMismatchError, match="Malformed"):
        strip_shared_bundles(app_dir)
    assert ep.read_text(encoding="utf-8") == tampered


@pytest.mark.parametrize(
    "edit",
    [
        lambda b: b.replace(
            "# === END", "# CONSOLE HOTFIX: keep until ticket-1\n# === END", 1
        ),
        lambda b: b.replace("_CXAS_DIGEST = ", "_CXAS_DIGEST =  ", 1),
        lambda b: b.replace(
            "    _cxas_mod = ", "    # note\n    _cxas_mod = ", 1
        ),
    ],
    ids=["comment_before_end", "extra_space", "comment_inside_guard"],
)
def test_comment_or_whitespace_edit_inside_block_raises(
    tmp_path: Path, edit: object
) -> None:
    app_dir, ep, bundled = _bundled_app(tmp_path)
    tampered = edit(bundled)  # type: ignore[operator]
    assert tampered != bundled
    ep.write_text(tampered, encoding="utf-8")

    with pytest.raises(BundleDigestMismatchError, match="unexpected content"):
        strip_shared_bundles(app_dir)
    assert ep.read_text(encoding="utf-8") == tampered


def test_crlf_bundle_strips(tmp_path: Path) -> None:
    app_dir, ep, bundled = _bundled_app(tmp_path)
    ep.write_bytes(bundled.replace("\n", "\r\n").encode("utf-8"))

    assert strip_shared_bundles(app_dir) == [ep]
    # Entrypoints are read/written in text mode (universal newlines), as before.
    assert ep.read_text(encoding="utf-8") == ENTRYPOINT_SRC
