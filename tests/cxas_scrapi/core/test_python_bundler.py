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

"""Unit tests for ``cxas_scrapi.core.python_bundler``."""

import ast
import types
from pathlib import Path

import pytest
import yaml

from cxas_scrapi.core.python_bundler import (
    BundleDigestMismatchError,
    BundleSizeError,
    bundle_shared_python,
    strip_shared_bundles,
    synthesize_python_env_files,
)


def test_ast_tree_shaking(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "guards.py").write_text(
        "from python_lib.common import x\ndef check(): return x()\n"
    )
    (plib / "common.py").write_text("def x(): return 42\n")
    (plib / "unused_heavy.py").write_text("import json\n# lots of stuff")

    agent_dir = (
        app_dir / "agents" / "my_agent" / "before_model_callbacks" / "cb_1"
    )
    agent_dir.mkdir(parents=True)
    ep = agent_dir / "python_code.py"
    ep.write_text(
        "from __future__ import annotations\nimport python_lib.guards\ndef before_model_callback(cb, llm_request):\n    return python_lib.guards.check()"
    )

    after_tool_dir = (
        app_dir / "agents" / "my_agent" / "after_tool_callbacks" / "cb_2"
    )
    after_tool_dir.mkdir(parents=True)
    ep2 = after_tool_dir / "python_code.py"
    ep2.write_text(
        "import python_lib.common\ndef after_tool_callback():\n    return python_lib.common.x()"
    )

    bundle_shared_python(app_dir)
    assert ep.is_file()
    content = ep.read_text()
    assert "unused_heavy" not in content
    assert "common" in content
    assert "guards" in content
    assert "CXAS_SHARED_BUNDLE" in ep2.read_text()
    # Topological sort check: common should be registered before guards
    common_idx = content.find("python_lib.common")
    guards_idx = content.find("python_lib.guards")
    assert common_idx < guards_idx

    # Verify module pre-registration in _cxas_sys.modules allows transitive from-import during exec
    sandbox_sys_modules: dict[str, types.ModuleType] = {}

    class FakeSys:
        modules = sandbox_sys_modules

    class FakeTypes:
        ModuleType = types.ModuleType

    scope = {"_cxas_sys": FakeSys, "_cxas_types": FakeTypes}
    exec(content, scope)
    assert scope["before_model_callback"](None, None) == 42


def test_pooled_sandbox_two_digests(tmp_path: Path) -> None:
    app_dir1 = tmp_path / "app1"
    app_dir2 = tmp_path / "app2"
    for ad in (app_dir1, app_dir2):
        plib = ad / "python_lib"
        plib.mkdir(parents=True)
        cb_dir = (
            ad
            / "agents"
            / "my_agent"
            / "my_agent_callbacks"
            / "before_model_callbacks"
        )
        cb_dir.mkdir(parents=True)
        (cb_dir / "python_code.py").write_text(
            "import python_lib.guards\ndef before_model_callback(): return python_lib.guards.VAL\n"
        )

    (app_dir1 / "python_lib" / "guards.py").write_text("VAL = 1")
    (app_dir2 / "python_lib" / "guards.py").write_text("VAL = 2")

    bundle_shared_python(app_dir1)
    bundle_shared_python(app_dir2)

    content1 = (
        app_dir1
        / "agents"
        / "my_agent"
        / "my_agent_callbacks"
        / "before_model_callbacks"
        / "python_code.py"
    ).read_text()
    content2 = (
        app_dir2
        / "agents"
        / "my_agent"
        / "my_agent_callbacks"
        / "before_model_callbacks"
        / "python_code.py"
    ).read_text()

    # Simulate CES execution
    sandbox_sys_modules = {}

    class FakeSys:
        modules = sandbox_sys_modules

    class FakeTypes:
        ModuleType = types.ModuleType

    scope1 = {"_cxas_sys": FakeSys, "_cxas_types": FakeTypes}
    exec(content1, scope1)
    assert scope1["before_model_callback"]() == 1

    scope2 = {"_cxas_sys": FakeSys, "_cxas_types": FakeTypes}
    exec(content2, scope2)
    assert scope2["before_model_callback"]() == 2


def test_ces_function_parser_constraints(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "guards.py").write_text("def helper(): pass")

    cb_dir = app_dir / "agents" / "a" / "a_callbacks" / "before_model_callbacks"
    cb_dir.mkdir(parents=True)
    cb_dir.joinpath("python_code.py").write_text(
        "import python_lib.guards\ndef before_model_callback(a: int) -> int: return 1"
    )

    bundle_shared_python(app_dir)
    content = cb_dir.joinpath("python_code.py").read_text()

    tree = ast.parse(content)
    first_func = next(
        (
            n
            for n in ast.iter_child_nodes(tree)
            if isinstance(n, ast.FunctionDef)
        ),
        None,
    )
    assert first_func.name == "before_model_callback"
    assert "__file__" not in content


def test_tampered_bundle_strip(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "guards.py").write_text("VAL = 1")
    cb_dir = app_dir / "agents" / "a" / "a_callbacks" / "before_model_callbacks"
    cb_dir.mkdir(parents=True)
    orig_ep = "import python_lib.guards\ndef x(): pass"
    cb_dir.joinpath("python_code.py").write_text(orig_ep)

    bundle_shared_python(app_dir)
    ep_file = cb_dir.joinpath("python_code.py")
    bundled = ep_file.read_text()

    # Tamper with digest
    tampered = bundled.replace("VAL = 1", "VAL = 2")
    ep_file.write_text(tampered)

    with pytest.raises(BundleDigestMismatchError):
        strip_shared_bundles(app_dir)

    strip_shared_bundles(app_dir, force=True)
    assert ep_file.read_text() == orig_ep


def test_bundle_size_limit(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "guards.py").write_text("x = " + repr("a" * 70000))
    cb_dir = app_dir / "agents" / "a" / "a_callbacks" / "before_model_callbacks"
    cb_dir.mkdir(parents=True)
    cb_dir.joinpath("python_code.py").write_text(
        "import python_lib.guards\ndef x(): pass"
    )

    with pytest.raises(BundleSizeError):
        bundle_shared_python(app_dir)


def test_native_python_env(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "guards.py").write_text("VAL = 1")
    cb_dir = app_dir / "agents" / "a" / "a_callbacks" / "before_model_callbacks"
    cb_dir.mkdir(parents=True)
    orig_ep = "import python_lib.guards\ndef x(): pass"
    cb_dir.joinpath("python_code.py").write_text(orig_ep)

    synthesize_python_env_files(app_dir)
    bundle_shared_python(app_dir, native_python_env=True)

    assert cb_dir.joinpath("python_code.py").read_text() == orig_ep
    yaml_path = app_dir / "pythonEnvFiles" / "pythonEnvFiles.yaml"
    assert yaml_path.exists()
    manifest = yaml.safe_load(yaml_path.read_text("utf-8"))
    assert isinstance(manifest["pythonEnvFiles"], list)
    assert manifest["pythonEnvFiles"] == [
        {
            "name": "python-lib-guards",
            "filePath": "content/python_lib/guards.py",
        }
    ]
