import types
import ast
import json
from pathlib import Path

import pytest

from cxas_scrapi.core.python_bundler import (
    BundleDigestMismatchError,
    BundleSizeError,
    bundle_shared_python,
    strip_shared_bundles,
    synthesize_python_env_files,
)
from cxas_scrapi.runtime import (
    MissingContentBlock,
    adapt_presentation,
    build_scoped_datastore_filter,
    check_disabled_agent,
    escape_search_filter_literal,
    get_verbatim_content_block,
    resolve_content_block_or_handoff,
)
from cxas_scrapi.utils.lint_rules.callbacks import VoiceBeforeModelToolMutation
from cxas_scrapi.utils.linter import LintContext


def test_ast_tree_shaking(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    plib.mkdir(parents=True)
    (plib / "guards.py").write_text("from python_lib import common\n")
    (plib / "common.py").write_text("def x(): pass\n")
    (plib / "unused_heavy.py").write_text("import json\n# lots of stuff")

    agent_dir = (
        app_dir
        / "agents"
        / "my_agent"
        / "my_agent_callbacks"
        / "before_model_callbacks"
    )
    agent_dir.mkdir(parents=True)
    ep = agent_dir / "python_code.py"
    ep.write_text(
        "from __future__ import annotations\nimport python_lib.guards\ndef before_model_callback(cb, llm_request):\n    pass"
    )

    bundle_shared_python(app_dir)
    assert ep.is_file()
    content = ep.read_text()
    assert "unused_heavy" not in content
    assert "common" in content
    assert "guards" in content
    # Topological sort check: common should be registered before guards
    common_idx = content.find("python_lib.common")
    guards_idx = content.find("python_lib.guards")
    assert common_idx < guards_idx


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
    assert (app_dir / "pythonEnvFiles" / "pythonEnvFiles.yaml").exists()


def test_fuzz_search_filter() -> None:
    with open(
        Path(__file__).parent.parent / "fixtures" / "search_filter_fuzz.jsonl"
    ) as f:
        for line in f:
            case = json.loads(line)
            if "error" in case:
                with pytest.raises(ValueError, match="Invalid character"):
                    escape_search_filter_literal(case["input"])
            else:
                assert (
                    escape_search_filter_literal(case["input"])
                    == case["expected"]
                )


def test_build_scoped_datastore_filter() -> None:
    state = {"context": {"brand_id": "B", "country_code": "US", "lang": "en"}}
    assert (
        build_scoped_datastore_filter(state)
        == 'brand: ANY("B") AND country: ANY("US") AND language: ANY("en")'
    )


def test_content_blocks() -> None:
    cat = {"B": {"US": {"en": {"k": "val"}}}}
    assert get_verbatim_content_block(cat, "B", "US", "en", "k") == "val"
    with pytest.raises(MissingContentBlock):
        get_verbatim_content_block(cat, "B", "UK", "en", "k")

    state = {"context": {"brand_id": "B", "country_code": "UK", "lang": "en"}}
    ans = resolve_content_block_or_handoff(
        state, cat, "k", fallback_utterance="fail"
    )
    assert ans == "fail"
    assert state["context"]["handoff_reason"] == "missing_statutory_copy"


def test_adapt_presentation() -> None:
    state = {"context": {"channel_class": "DIGITAL_APP"}}
    res = adapt_presentation(state, "hello", {"w": 1})
    assert res["channel_mode"] == "digital"
    assert res["widget_json"] == '{"w": 1}'

    state_voice = {"context": {"channel_class": "VOICE_TELEPHONY"}}
    res2 = adapt_presentation(state_voice, "hello [link](http://x) **bold**")
    assert res2["channel_mode"] == "voice"
    assert res2["text"] == "hello link bold"


def test_check_disabled_agent() -> None:
    state = {"context": {"disabled_agents": ["a1"]}}
    res = check_disabled_agent(state, "a1", fallback_message="out")
    assert res["short_circuit"]
    assert check_disabled_agent(state, "a2", fallback_message="out") is None


def test_c011_rule(tmp_path: Path) -> None:
    cb_file = tmp_path / "before_model_callbacks" / "python_code.py"
    cb_file.parent.mkdir()
    code = "def before_model_callback(cb, req):\n    req.hide_tool('x')\n    req.config = 1\n"
    cb_file.write_text(code)

    # Needs a context with voice enabled
    app_root = tmp_path
    (app_root / "app.json").write_text('{"audioProcessingConfig": {"x": 1}}')
    ctx = LintContext(project_root=tmp_path, app_dir=tmp_path, evals_dir=tmp_path)
    ctx.app_root = app_root

    rule = VoiceBeforeModelToolMutation()
    res = rule.check(cb_file, code, ctx)
    assert len(res) == 2
    assert "mutations" in res[0].message
