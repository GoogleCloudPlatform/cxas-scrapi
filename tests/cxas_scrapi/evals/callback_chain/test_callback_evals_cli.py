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

"""CallbackEvals and CLI (`cxas test-callbacks`) integration tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pandas as pd
import pytest

from cxas_scrapi.cli.main import (
    get_parser,
)
from cxas_scrapi.cli.main import (
    test_callbacks as cli_test_callbacks,
)
from cxas_scrapi.evals.callback_evals import CHAIN_MODE_ENV, CallbackEvals


@patch("cxas_scrapi.evals.callback_evals.Apps")
@patch("cxas_scrapi.evals.callback_evals.Agents")
def test_callback_evals_single_callback_for_agent_supports_multiple_callbacks(
    mock_agents_cls: MagicMock, mock_apps_cls: MagicMock, tmp_path: Path
) -> None:
    """Verifies test_single_callback_for_agent chains global and agent callbacks."""
    from google.cloud.ces_v1beta import types  # noqa: PLC0415

    app_uri = "projects/p1/locations/us-central1/apps/app1"
    mock_agents = mock_agents_cls.return_value
    mock_agents.get_agents_map.return_value = {
        "budget_agent": f"{app_uri}/agents/a1"
    }

    real_app = types.App(name=app_uri)
    real_app.__dict__["global_before_model_callbacks"] = [
        types.Callback(
            disabled=False,
            description="global_bm_01",
            python_code=(
                "def before_model_callback(callback_context, llm_request):\n"
                "    callback_context.state['seq'] = ['global']\n"
            ),
        )
    ]
    mock_apps_cls.return_value.get_app.return_value = real_app

    real_agent = types.Agent(
        name=f"{app_uri}/agents/a1",
        display_name="budget_agent",
        before_model_callbacks=[
            types.Callback(
                disabled=False,
                description="bm_01",
                python_code=(
                    "def before_model_callback(callback_context, llm_request):\n"
                    "    callback_context.state.setdefault('seq', []).append('cb1')\n"
                ),
            ),
            types.Callback(
                disabled=True,
                description="bm_02_disabled",
                python_code=(
                    "def before_model_callback(callback_context, llm_request):\n"
                    "    callback_context.state['seq'].append('cb2_disabled')\n"
                ),
            ),
            types.Callback(
                disabled=False,
                description="bm_03",
                python_code=(
                    "def before_model_callback(callback_context, llm_request):\n"
                    "    if callback_context.state.get('should_fail'):\n"
                    "        raise RuntimeError('boom in second enabled agent callback')\n"
                    "    callback_context.state['seq'].append('cb3')\n"
                    "    return LlmResponse.from_parts([Part.from_text('done')])\n"
                ),
            ),
        ],
    )
    mock_agents.get_agent.return_value = real_agent

    test_file = tmp_path / "test_multi.py"
    test_file.write_text(
        "import pytest\n"
        "from python_code import before_model_callback, CallbackContext, LlmRequest\n"
        "def test_chained_execution():\n"
        "    ctx = CallbackContext(state={})\n"
        "    res = before_model_callback(ctx, LlmRequest())\n"
        "    assert ctx.state['seq'] == ['global', 'cb1', 'cb3']\n"
        "    assert res.content.parts[0].text == 'done'\n\n"
        "def test_exception_in_second_callback_is_observed():\n"
        "    ctx = CallbackContext(state={'should_fail': True})\n"
        "    with pytest.raises(RuntimeError, match='boom in second enabled agent callback'):\n"
        "        before_model_callback(ctx, LlmRequest())\n"
    )

    evals = CallbackEvals()
    df = evals.test_single_callback_for_agent(
        app_name=app_uri,
        agent_name="budget_agent",
        callback_type="before_model",
        test_file_path=str(test_file),
    )

    assert len(df) == 2
    assert list(df["status"]) == ["PASSED", "PASSED"]


def test_callback_evals_mixed_discovery_filtering_and_dual_mode(
    tmp_path: Path,
) -> None:
    """Verifies test_all_callbacks_in_app_dir discovers both test.py and test_chain.py."""
    app_dir = tmp_path / "sample_app"
    agent_dir = app_dir / "agents" / "card_agent"
    cb1_dir = agent_dir / "before_agent_callbacks" / "before_agent_callbacks_01"
    cb2_dir = agent_dir / "before_agent_callbacks" / "before_agent_callbacks_02"
    bm_dir = agent_dir / "before_model_callbacks" / "before_model_callbacks_01"
    cb1_dir.mkdir(parents=True)
    cb2_dir.mkdir(parents=True)
    bm_dir.mkdir(parents=True)

    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "name": "sample_app",
                "variableDeclarations": [
                    {
                        "name": "default_flag",
                        "schema": {"type": "BOOLEAN", "default": True},
                    }
                ],
            }
        )
    )
    (agent_dir / "card_agent.json").write_text(
        json.dumps(
            {
                "name": "card_agent",
                "beforeAgentCallbacks": [
                    {
                        "pythonCode": (
                            "agents/card_agent/before_agent_callbacks/"
                            "before_agent_callbacks_01/python_code.py"
                        )
                    },
                    {
                        "pythonCode": (
                            "agents/card_agent/before_agent_callbacks/"
                            "before_agent_callbacks_02/python_code.py"
                        )
                    },
                ],
                "beforeModelCallbacks": [
                    {
                        "pythonCode": (
                            "agents/card_agent/before_model_callbacks/"
                            "before_model_callbacks_01/python_code.py"
                        )
                    }
                ],
            }
        )
    )
    (cb1_dir / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['visited'] = ['01']\n"
    )
    (cb1_dir / "test.py").write_text(
        "from python_code import before_agent_callback, CallbackContext\n"
        "def test_unit_cb1():\n"
        "    ctx = CallbackContext()\n"
        "    before_agent_callback(ctx)\n"
        "    assert ctx.state['visited'] == ['01']\n"
    )
    (cb2_dir / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['visited'].append('02')\n"
    )
    (bm_dir / "python_code.py").write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.state['bm'] = True\n"
    )
    (bm_dir / "test.py").write_text(
        "from python_code import before_model_callback, CallbackContext, LlmRequest\n"
        "def test_unit_bm1():\n"
        "    ctx = CallbackContext()\n"
        "    before_model_callback(ctx, LlmRequest())\n"
        "    assert ctx.state['bm'] is True\n"
    )
    (agent_dir / "before_agent_callbacks" / "test_chain.py").write_text(
        "from python_code import runner, CHAIN_MODE, CallbackContext\n"
        "def test_stage_chain():\n"
        "    assert runner.mode == CHAIN_MODE\n"
        "    ctx = CallbackContext()\n"
        "    _, trace = runner.run_before_agent(ctx)\n"
        "    assert ctx.state['default_flag'] is True\n"
        "    assert ctx.state['visited'] == ['01', '02']\n"
        "    assert trace.mode == CHAIN_MODE\n"
    )

    evals = CallbackEvals()

    # 1. Mixed discovery with mode="both" runs 2 unit tests + 2 chain runs (per_callback + combined)
    df_all = evals.test_all_callbacks_in_app_dir(str(app_dir), mode="both")
    assert len(df_all) == 4
    assert set(df_all["test_name"]) == {
        "test_unit_cb1",
        "test_unit_bm1",
        "test_stage_chain[per_callback]",
        "test_stage_chain[combined]",
    }
    assert all(s == "PASSED" for s in df_all["status"])

    # 2. Filter by callback_type="before_model_callbacks"
    df_bm = evals.test_all_callbacks_in_app_dir(
        str(app_dir), callback_type="before_model_callbacks"
    )
    assert list(df_bm["test_name"]) == ["test_unit_bm1"]

    # 3. chain_only=True skips unit tests
    df_chain_only = evals.test_all_callbacks_in_app_dir(
        str(app_dir), chain_only=True, mode="combined"
    )
    assert list(df_chain_only["test_name"]) == ["test_stage_chain"]

    # 4. include_chain=False skips chain tests
    df_no_chain = evals.test_all_callbacks_in_app_dir(
        str(app_dir), include_chain=False
    )
    assert set(df_no_chain["test_name"]) == {"test_unit_cb1", "test_unit_bm1"}


def test_cli_test_callbacks_argparse_and_skipped_status_handling(
    tmp_path: Path,
) -> None:
    """Verifies CLI test-callbacks parses --mode / --chain-only and ignores SKIPPED."""
    parser = get_parser()
    args = parser.parse_args(
        [
            "test-callbacks",
            "--app-dir",
            str(tmp_path),
            "--agent-name",
            "budget_agent",
            "--mode",
            "both",
            "--chain-only",
        ]
    )
    assert args.mode == "both"
    assert args.chain_only is True
    assert args.no_chain is False

    with patch(
        "cxas_scrapi.evals.callback_evals.CallbackEvals.test_all_callbacks_in_app_dir"
    ) as mock_run:
        mock_run.return_value = pd.DataFrame(
            [
                {
                    "agent_name": "budget_agent",
                    "callback_type": "chain_callbacks",
                    "test_name": "test_1",
                    "status": "PASSED",
                    "error_message": None,
                },
                {
                    "agent_name": "budget_agent",
                    "callback_type": "chain_callbacks",
                    "test_name": "test_2",
                    "status": "SKIPPED",
                    "error_message": None,
                },
            ]
        )
        with pytest.raises(SystemExit) as exc_info:
            cli_test_callbacks(args)
        assert exc_info.value.code == 0
        mock_run.assert_called_once_with(
            app_dir=str(tmp_path),
            agent_name="budget_agent",
            callback_type=None,
            callback_name=None,
            log_file=None,
            pytest_args=None,
            mode="both",
            include_chain=True,
            chain_only=True,
        )


def test_user_constructed_runner_in_test_chain_respects_mode_env(
    tmp_path: Path,
) -> None:
    """Verifies user-constructed runner in test_chain.py respects CHAIN_MODE_ENV."""
    assert CHAIN_MODE_ENV == "CXAS_CALLBACK_MODE"

    app_dir = tmp_path / "env_app"
    agent_dir = app_dir / "agents" / "demo_agent"
    cb_dir = agent_dir / "before_agent_callbacks" / "cb_01"
    cb_dir.mkdir(parents=True)

    (app_dir / "app.json").write_text(json.dumps({"name": "env_app"}))
    (agent_dir / "demo_agent.json").write_text(
        json.dumps(
            {
                "name": "demo_agent",
                "beforeAgentCallbacks": [
                    {
                        "pythonCode": (
                            "agents/demo_agent/before_agent_callbacks/cb_01/python_code.py"
                        )
                    }
                ],
            }
        )
    )
    (cb_dir / "python_code.py").write_text(
        "def before_agent_callback(ctx):\n    ctx.state['ok'] = True\n"
    )
    (agent_dir / "test_chain.py").write_text(
        "from cxas_scrapi.evals.callback_evals import CallbackChainRunner\n"
        "from python_code import AGENT_DIR, APP_DIR, CHAIN_MODE, CallbackContext\n"
        "def test_user_runner_gets_cli_mode():\n"
        "    custom_runner = CallbackChainRunner.from_agent_dir(AGENT_DIR, app_dir=APP_DIR)\n"
        "    assert custom_runner.mode == CHAIN_MODE\n"
        "    _, trace = custom_runner.run_before_agent(CallbackContext())\n"
        "    assert trace.mode == CHAIN_MODE\n"
    )

    evals = CallbackEvals()
    df_comb = evals.test_all_callbacks_in_app_dir(
        str(app_dir), mode="combined", chain_only=True
    )
    assert len(df_comb) == 1
    assert df_comb.iloc[0]["status"] == "PASSED"

    df_both = evals.test_all_callbacks_in_app_dir(
        str(app_dir), mode="both", chain_only=True
    )
    assert len(df_both) == 2
    assert list(df_both["status"]) == ["PASSED", "PASSED"]


@patch("cxas_scrapi.evals.callback_evals.Apps")
@patch("cxas_scrapi.evals.callback_evals.Agents")
def test_single_callback_for_agent_fetches_global_callbacks_and_app_config(
    mock_agents_cls: MagicMock,
    mock_apps_cls: MagicMock,
    tmp_path: Path,
) -> None:
    """Verifies test_single_callback_for_agent loads global callbacks and app config."""
    from google.cloud.ces_v1beta import types  # noqa: PLC0415

    app_uri = "projects/p1/locations/us-central1/apps/a1"
    mock_agents = mock_agents_cls.return_value
    mock_agents.get_agents_map.return_value = {
        "budget_agent": f"{app_uri}/agents/ag1"
    }

    real_agent = types.Agent(
        name=f"{app_uri}/agents/ag1",
        display_name="budget_agent",
        before_model_callbacks=[
            types.Callback(
                disabled=False,
                description="agent_bm_01",
                python_code=(
                    "def before_model_callback(callback_context, llm_request):\n"
                    "    tier = callback_context.state['env_tier']\n"
                    "    rb = int(callback_context.state['retry_budget'])\n"
                    "    callback_context.state['log'].append(f'agent:{tier}:{rb}')\n"
                ),
            )
        ],
    )
    mock_agents.get_agent.return_value = real_agent

    real_app = types.App(
        name=app_uri,
        variable_declarations=[
            types.App.VariableDeclaration(
                name="env_tier",
                schema=types.Schema(
                    type_=types.Schema.Type.STRING,
                    default="production",
                ),
            ),
            types.App.VariableDeclaration(
                name="retry_budget",
                schema=types.Schema(
                    type_=types.Schema.Type.INTEGER,
                    default=3,
                ),
            ),
        ],
        error_handling_settings=types.ErrorHandlingSettings(
            error_handling_strategy=types.ErrorHandlingSettings.ErrorHandlingStrategy.END_SESSION,
            end_session_config=types.ErrorHandlingSettings.EndSessionConfig(
                escalate_session=True
            ),
        ),
    )
    real_app.__dict__["global_before_model_callbacks"] = [
        types.Callback(
            disabled=False,
            description="app_global_bm_01",
            python_code=(
                "def before_model_callback(callback_context, llm_request):\n"
                "    callback_context.state['log'] = ['app_global']\n"
            ),
        )
    ]
    mock_apps_cls.return_value.get_app.return_value = real_app

    test_file = tmp_path / "test_app_proto.py"
    test_file.write_text(
        "from python_code import before_model_callback, runner, CallbackContext, LlmRequest\n"
        "def test_app_proto_globals_and_vars():\n"
        "    ctx = CallbackContext(state={})\n"
        "    before_model_callback(ctx, LlmRequest())\n"
        "    assert ctx.state['env_tier'] == 'production'\n"
        "    assert ctx.state['retry_budget'] == 3.0\n"
        "    assert ctx.state['log'] == ['app_global', 'agent:production:3']\n"
        "    assert runner.error_handling_settings['error_handling_strategy'] == 'END_SESSION'\n"
    )

    evals = CallbackEvals()
    df = evals.test_single_callback_for_agent(
        app_name=app_uri,
        agent_name="budget_agent",
        callback_type="before_model",
        test_file_path=str(test_file),
    )
    mock_apps_cls.return_value.get_app.assert_called_once_with(app_uri)
    assert len(df) == 1
    assert df.iloc[0]["status"] == "PASSED"


def test_callback_evals_and_cli_lint_and_compare_modes(
    tmp_path: Path,
) -> None:
    """Verifies CallbackEvals and CLI `--lint`, `--lint-only`, and `--compare-modes`."""
    app_dir = tmp_path / "m3_app"
    agent_dir = app_dir / "agents" / "demo_agent"
    cb1_dir = agent_dir / "before_agent_callbacks" / "cb1"
    cb2_dir = agent_dir / "before_agent_callbacks" / "cb2"
    cb1_dir.mkdir(parents=True)
    cb2_dir.mkdir(parents=True)

    (app_dir / "app.json").write_text(json.dumps({"name": "m3_app"}))
    (agent_dir / "demo_agent.json").write_text(
        json.dumps(
            {
                "name": "demo_agent",
                "beforeAgentCallbacks": [
                    {
                        "pythonCode": "agents/demo_agent/before_agent_callbacks/cb1/python_code.py"
                    },
                    {
                        "pythonCode": "agents/demo_agent/before_agent_callbacks/cb2/python_code.py"
                    },
                ],
            }
        )
    )
    (cb1_dir / "python_code.py").write_text(
        "def before_agent_callback(ctx):\n"
        "    box = {'items': [1, 2]}\n"
        "    ctx.state['a'] = box\n"
        "    ctx.state['b'] = box\n"
    )
    (cb2_dir / "python_code.py").write_text(
        "def before_agent_callback(ctx):\n    ctx.state['b']['items'].pop()\n"
    )

    evals = CallbackEvals()
    lint_df = evals.lint_all_callbacks_in_app_dir(str(app_dir))
    assert len(lint_df) == 1
    assert "C012" in lint_df.iloc[0]["test_name"]
    assert lint_df.iloc[0]["status"] == "WARNING"

    cmp_df = evals.compare_all_callbacks_in_app_dir(str(app_dir))
    assert len(cmp_df) == 1
    assert cmp_df.iloc[0]["status"] == "FAILED"
    assert "C012" in (cmp_df.iloc[0]["error_message"] or "")

    parser = get_parser()
    args_lint = parser.parse_args(
        ["test-callbacks", "--app-dir", str(app_dir), "--lint-only"]
    )
    with pytest.raises(SystemExit) as exc_lint:
        cli_test_callbacks(args_lint)
    assert exc_lint.value.code == 0

    args_cmp = parser.parse_args(
        ["test-callbacks", "--app-dir", str(app_dir), "--compare-modes"]
    )
    with pytest.raises(SystemExit) as exc_cmp:
        cli_test_callbacks(args_cmp)
    assert exc_cmp.value.code == 1


def test_compare_fixtures_statuses_and_cli_constraints(
    tmp_path: Path,
) -> None:
    """Verifies compare_all_callbacks statuses, fixtures, and CLI flag constraints."""
    from cxas_scrapi.evals.callback_chain import (  # noqa: PLC0415
        CallbackChainRunner,
    )
    from cxas_scrapi.utils.callback_libs import (  # noqa: PLC0415
        CallbackContext,
    )

    app_dir = tmp_path / "r5_app"
    agent_dir = app_dir / "agents" / "budget_agent"
    ba1_dir = agent_dir / "before_agent_callbacks" / "ba_01"
    ba2_dir = agent_dir / "before_agent_callbacks" / "ba_02"
    bm1_dir = agent_dir / "before_model_callbacks" / "bm_01"
    am1_dir = agent_dir / "after_model_callbacks" / "am_01"
    for d in (ba1_dir, ba2_dir, bm1_dir, am1_dir):
        d.mkdir(parents=True)

    (app_dir / "app.json").write_text(json.dumps({"name": "r5_app"}))
    (agent_dir / "budget_agent.json").write_text(
        json.dumps(
            {
                "name": "budget_agent",
                "beforeAgentCallbacks": [
                    {
                        "pythonCode": (
                            "agents/budget_agent/before_agent_callbacks/ba_01/python_code.py"
                        )
                    },
                    {
                        "pythonCode": (
                            "agents/budget_agent/before_agent_callbacks/ba_02/python_code.py"
                        )
                    },
                ],
                "beforeModelCallbacks": [
                    {
                        "pythonCode": (
                            "agents/budget_agent/before_model_callbacks/bm_01/python_code.py"
                        )
                    }
                ],
                "afterModelCallbacks": [
                    {
                        "pythonCode": (
                            "agents/budget_agent/after_model_callbacks/am_01/python_code.py"
                        )
                    }
                ],
            }
        )
    )
    # ba_01 + ba_02: C012 aliasing only reached when caller_data is populated
    (ba1_dir / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    if callback_context.state.get('caller_data'):\n"
        "        box = {'items': [1, 2]}\n"
        "        callback_context.state['all_acc'] = box\n"
        "        callback_context.state['id_acc'] = box\n"
    )
    (ba2_dir / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    acc = callback_context.state.get('id_acc', {})\n"
        "    if 'items' in acc:\n"
        "        acc['items'].pop()\n"
    )
    # bm_01: no-op on empty state -> INCONCLUSIVE
    (bm1_dir / "python_code.py").write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    if callback_context.state.get('never_set'):\n"
        "        callback_context.state['x'] = 1\n"
    )
    # am_01: raises error -> ERROR
    (am1_dir / "python_code.py").write_text(
        "def after_model_callback(callback_context, llm_response):\n"
        "    raise RuntimeError('am boom')\n"
    )

    evals = CallbackEvals()
    cmp_df = evals.compare_all_callbacks_in_app_dir(str(app_dir))
    by_type = {row["callback_type"]: row for _, row in cmp_df.iterrows()}

    # Without fixtures: before_agent is RISK (C012 static finding),
    # before_model is INCONCLUSIVE, after_model is ERROR
    assert by_type["before_agent_callbacks"]["status"] == "RISK"
    assert "C012" in by_type["before_agent_callbacks"]["error_message"]
    assert by_type["before_model_callbacks"]["status"] == "INCONCLUSIVE"
    assert by_type["after_model_callbacks"]["status"] == "ERROR"
    assert "am boom" in by_type["after_model_callbacks"]["error_message"]

    # Record a fixture via runner.record_fixture and verify compare_modes_from_fixture
    runner = CallbackChainRunner.from_agent_dir(agent_dir, app_dir=app_dir)
    fx_path = runner.record_fixture(
        CallbackContext(state={"caller_data": True}),
        "before_agent",
        agent_dir / "compare_fixtures" / "before_agent" / "with_caller.json",
    )
    diff_fx = runner.compare_modes_from_fixture(fx_path)
    assert diff_fx.has_diff is True

    cmp_df_with_fx = evals.compare_all_callbacks_in_app_dir(
        str(app_dir), callback_type="before_agent"
    )
    assert len(cmp_df_with_fx) == 1
    assert cmp_df_with_fx.iloc[0]["status"] == "FAILED"
    assert "with_caller" in cmp_df_with_fx.iloc[0]["test_name"]

    # Automatic fixture recording via test_all_callbacks_in_app_dir(..., record_fixtures=True)
    (agent_dir / "before_model_callbacks" / "test_chain.py").write_text(
        "from python_code import runner, CallbackContext, LlmRequest\n"
        "def test_bm_record():\n"
        "    ctx = CallbackContext(state={'never_set': True})\n"
        "    runner.run_before_model(ctx, LlmRequest())\n"
    )
    evals.test_all_callbacks_in_app_dir(
        str(app_dir),
        callback_type="before_model_callbacks",
        chain_only=True,
        record_fixtures=True,
    )
    recorded_bm = list(
        (agent_dir / "compare_fixtures" / "before_model").glob("*.json")
    )
    assert len(recorded_bm) >= 1
    cmp_bm = evals.compare_all_callbacks_in_app_dir(
        str(app_dir), callback_type="before_model"
    )
    assert cmp_bm.iloc[0]["status"] == "PASSED"

    # Broken agent in lint_all_callbacks_in_app_dir emits C019 FAILED row
    broken_dir = app_dir / "agents" / "broken_agent"
    broken_dir.mkdir(parents=True)
    (broken_dir / "broken_agent.json").write_text("{malformed json")
    lint_df = evals.lint_all_callbacks_in_app_dir(
        str(app_dir), agent_name="broken_agent"
    )
    assert len(lint_df) == 1
    assert lint_df.iloc[0]["status"] == "FAILED"
    assert "C019" in lint_df.iloc[0]["test_name"]

    # CLI mutually exclusive flags and incompatible flag combinations exit with code 2
    parser = get_parser()
    with pytest.raises(SystemExit) as exc_mutex:
        parser.parse_args(
            [
                "test-callbacks",
                "--app-dir",
                str(app_dir),
                "--lint-only",
                "--compare-modes",
            ]
        )
    assert exc_mutex.value.code == 2

    args_incompat = parser.parse_args(
        [
            "test-callbacks",
            "--app-dir",
            str(app_dir),
            "--lint-only",
            "--chain-only",
        ]
    )
    with pytest.raises(SystemExit) as exc_incompat:
        cli_test_callbacks(args_incompat)
    assert exc_incompat.value.code == 2


def test_fixture_dedup_session_init_bytes_and_fail_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies fixture deduplication, session_initialized, Blob bytes, and --fail-on."""
    from cxas_scrapi.evals.callback_chain import (  # noqa: PLC0415
        CallbackChainRunner,
    )
    from cxas_scrapi.utils.callback_libs import (  # noqa: PLC0415
        Blob,
        CallbackContext,
        LlmResponse,
        Part,
    )

    app_dir = tmp_path / "r6_app"
    ag_dir = app_dir / "agents" / "a1"
    (ag_dir / "before_agent_callbacks" / "cb1").mkdir(parents=True)
    (ag_dir / "before_agent_callbacks" / "cb1" / "python_code.py").write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['seen_v'] = 'v' in callback_context.state\n"
    )
    (ag_dir / "a1.json").write_text(
        json.dumps(
            {
                "displayName": "a1",
                "beforeAgentCallbacks": [
                    {
                        "pythonCode": "agents/a1/before_agent_callbacks/cb1/python_code.py"
                    }
                ],
            }
        )
    )
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "variableDeclarations": [
                    {
                        "name": "v",
                        "schema": {"type": "STRING", "default": "x"},
                    }
                ]
            }
        )
    )

    r = CallbackChainRunner.from_agent_dir(ag_dir, app_dir=app_dir)

    # 2 identical compare_modes calls with CXAS_RECORD_FIXTURES=1 write 1 deduplicated file
    ext_fx_dir = tmp_path / "external_fixtures"
    monkeypatch.setenv("CXAS_RECORD_FIXTURES", "1")
    monkeypatch.setenv("CXAS_FIXTURES_DIR", str(ext_fx_dir))
    r.compare_modes(CallbackContext(state={"k": 1}), stage="before_agent")
    r.compare_modes(CallbackContext(state={"k": 1}), stage="before_agent")
    monkeypatch.delenv("CXAS_RECORD_FIXTURES")
    monkeypatch.delenv("CXAS_FIXTURES_DIR")

    files = sorted((ext_fx_dir / "a1" / "before_agent").glob("*.json"))
    assert len(files) == 1
    assert not (ag_dir / "compare_fixtures").exists()

    # Fixture recorded on an already-initialized ctx preserves session_initialized=True
    ctx = CallbackContext(state={})
    r.run_before_agent(ctx)
    ctx.state.pop("v")
    _, tr = r.run_before_agent(ctx)
    assert tr.final_state["seen_v"] is False
    fx_y2 = r.record_fixture(
        ctx, "before_agent", output_path=tmp_path / "y2.json"
    )
    rep_y2 = r.compare_modes_from_fixture(fx_y2)
    assert rep_y2.per_callback_trace.final_state["seen_v"] is False

    # Blob.data bytes losslessly base64 serialized and restored; unserializable state fails
    raw_bytes = b"\x89PNG\r\n\x1a\n"
    resp = LlmResponse.from_parts(
        [Part(inline_data=Blob(data=raw_bytes, mime_type="image/png"))]
    )
    fx_y3 = r.record_fixture(
        CallbackContext(state={}),
        "after_model",
        output_path=tmp_path / "y3.json",
        llm_response=resp,
    )
    raw_json = json.loads(fx_y3.read_text(encoding="utf-8"))
    b64_str = raw_json["llm_response"]["content"]["parts"][0]["inline_data"][
        "data"
    ]
    assert b64_str == "iVBORw0KGgo="
    rep_y3 = r.compare_modes_from_fixture(fx_y3)
    restored_bytes = rep_y3.per_callback_trace.effective_response.content.parts[
        0
    ].inline_data.data
    assert restored_bytes == raw_bytes

    with pytest.raises((TypeError, ValueError)):
        r.record_fixture(
            CallbackContext(state={"bad": object()}),
            "before_agent",
            output_path=tmp_path / "bad.json",
        )

    # --fail-on failed,error allows RISK status to exit 0
    parser = get_parser()
    with patch(
        "cxas_scrapi.evals.callback_evals.CallbackEvals.compare_all_callbacks_in_app_dir"
    ) as mock_cmp:
        mock_cmp.return_value = pd.DataFrame(
            [
                {
                    "agent_name": "a1",
                    "callback_type": "before_agent_callbacks",
                    "test_name": "compare_modes:before_agent",
                    "status": "RISK",
                    "error_message": "[C012] cb1:1: shared ref",
                }
            ]
        )
        args_default = parser.parse_args(
            ["test-callbacks", "--app-dir", str(app_dir), "--compare-modes"]
        )
        with pytest.raises(SystemExit) as exc_def:
            cli_test_callbacks(args_default)
        assert exc_def.value.code == 1

        args_ignore_risk = parser.parse_args(
            [
                "test-callbacks",
                "--app-dir",
                str(app_dir),
                "--compare-modes",
                "--fail-on",
                "failed,error",
            ]
        )
        with pytest.raises(SystemExit) as exc_ok:
            cli_test_callbacks(args_ignore_risk)
        assert exc_ok.value.code == 0


def test_test_callbacks_llm_mutex_and_include_metrics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verifies test-callbacks --llm mutex and CallbackEvals metrics summary."""
    from cxas_scrapi.evals.callback_chain import (  # noqa: PLC0415
        SessionMetricsSummary,
    )

    app_dir = tmp_path / "cli_app"
    ag_dir = app_dir / "agents" / "root_agent"
    sub_dir = app_dir / "agents" / "sub_agent"
    ag_dir.mkdir(parents=True)
    sub_dir.mkdir(parents=True)
    (ag_dir / "instruction.txt").write_text("Root tier={{tier}}")
    (sub_dir / "instruction.txt").write_text("Sub tier={{tier}}")
    (ag_dir / "root_agent.json").write_text(
        json.dumps(
            {
                "name": "root_agent",
                "displayName": "root_agent",
                "instruction": "agents/root_agent/instruction.txt",
            }
        )
    )
    (sub_dir / "sub_agent.json").write_text(
        json.dumps(
            {
                "name": "sub_agent",
                "displayName": "sub_agent",
                "instruction": "agents/sub_agent/instruction.txt",
            }
        )
    )
    (app_dir / "app.json").write_text(json.dumps({"rootAgent": "root_agent"}))

    parser = get_parser()

    # 1. --llm mutex with --lint-only and --compare-modes in test-callbacks
    for mode_flag in ("--lint-only", "--compare-modes"):
        args_mutex = parser.parse_args(
            [
                "test-callbacks",
                "--app-dir",
                str(app_dir),
                mode_flag,
                "--llm",
                "gemini-2.5-flash",
            ]
        )
        with pytest.raises(SystemExit) as exc_mutex:
            cli_test_callbacks(args_mutex)
        assert exc_mutex.value.code == 2
        err_out = capsys.readouterr().err
        assert "--llm" in err_out

    # 2. CallbackEvals(..., include_metrics=True) attaches SessionMetricsSummary
    (ag_dir / "test_chain.py").write_text(
        "from python_code import SessionSimulator, runner\n"
        "from cxas_scrapi.utils.callback_libs import LlmResponse, Part, UsageMetadata\n"
        "def test_chain_metrics():\n"
        "    sim = SessionSimulator(runner)\n"
        "    sim.turn(\n"
        "        'hi',\n"
        "        mock_llm=LlmResponse.from_parts(\n"
        "            [Part.from_text('ok')],\n"
        "            usage_metadata=UsageMetadata(\n"
        "                prompt_token_count=15, candidates_token_count=5\n"
        "            ),\n"
        "        ),\n"
        "    )\n",
        encoding="utf-8",
    )
    evals = CallbackEvals()
    df_metrics = evals.test_all_callbacks_in_app_dir(
        str(app_dir),
        agent_name="root_agent",
        chain_only=True,
        include_metrics=True,
    )
    assert "metrics_summary" in df_metrics.columns
    summary = df_metrics.iloc[0]["metrics_summary"]
    assert isinstance(summary, SessionMetricsSummary)
    assert summary.turn_count == 1
    assert summary.total_tokens == 20
    assert (
        evals.last_metrics_summaries[
            "root_agent/chain_callbacks/test_chain_metrics"
        ].total_tokens
        == 20
    )


def test_metrics_summaries_multi_agent_clear_mode_both_and_xdist_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verifies unique metrics keys, clearing, mode=both, and xdist warning."""
    app_dir = tmp_path / "app_metrics"
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "app_metrics", "rootAgent": "agent_a"}),
        encoding="utf-8",
    )
    for ag_name, tok in [("agent_a", 10), ("agent_b", 99)]:
        ag_dir = app_dir / "agents" / ag_name
        ag_dir.mkdir(parents=True)
        (ag_dir / f"{ag_name}.json").write_text(
            json.dumps(
                {"displayName": ag_name, "instruction": "instruction.txt"}
            ),
            encoding="utf-8",
        )
        (ag_dir / "instruction.txt").write_text("hi", encoding="utf-8")
        (ag_dir / "test_chain.py").write_text(
            f"from cxas_scrapi.evals.callback_chain import SessionSimulator\n"
            f"from cxas_scrapi.utils.callback_libs import (\n"
            f"    LlmResponse, Part, UsageMetadata\n"
            f")\n"
            f"from python_code import runner\n"
            f"def test_chain_metrics():\n"
            f"    sim = SessionSimulator(runner)\n"
            f"    sim.turn(\n"
            f"        'hi',\n"
            f"        mock_llm=LlmResponse.from_parts(\n"
            f"            [Part.from_text('ok')],\n"
            f"            usage_metadata=UsageMetadata(\n"
            f"                prompt_token_count={tok},\n"
            f"                candidates_token_count=1,\n"
            f"                total_token_count={tok + 1},\n"
            f"            ),\n"
            f"        ),\n"
            f"    )\n",
            encoding="utf-8",
        )

    evals = CallbackEvals()

    # 1. Multi-agent run with identical test_name does not collide
    df_all = evals.test_all_callbacks_in_app_dir(
        str(app_dir), include_metrics=True
    )
    assert len(df_all) == 2
    assert [
        r["metrics_summary"].prompt_tokens for _, r in df_all.iterrows()
    ] == [10, 99]
    assert set(evals.last_metrics_summaries.keys()) == {
        "agent_a/chain_callbacks/test_chain_metrics",
        "agent_b/chain_callbacks/test_chain_metrics",
    }
    assert (
        evals.last_metrics_summaries[
            "agent_a/chain_callbacks/test_chain_metrics"
        ].prompt_tokens
        == 10
    )
    assert (
        evals.last_metrics_summaries[
            "agent_b/chain_callbacks/test_chain_metrics"
        ].prompt_tokens
        == 99
    )

    # 2. Second call scoped to agent_a clears stale agent_b entries
    df_a = evals.test_all_callbacks_in_app_dir(
        str(app_dir), agent_name="agent_a", include_metrics=True
    )
    assert len(df_a) == 1
    assert set(evals.last_metrics_summaries.keys()) == {
        "agent_a/chain_callbacks/test_chain_metrics",
    }

    # 3. mode="both" suffixes keys with [per_callback] and [combined] without stale unsuffixed key
    df_both = evals.test_all_callbacks_in_app_dir(
        str(app_dir),
        agent_name="agent_a",
        mode="both",
        include_metrics=True,
    )
    assert len(df_both) == 2
    assert set(evals.last_metrics_summaries.keys()) == {
        "agent_a/chain_callbacks/test_chain_metrics[per_callback]",
        "agent_a/chain_callbacks/test_chain_metrics[combined]",
    }
    assert (
        "agent_a/chain_callbacks/test_chain_metrics"
        not in evals.last_metrics_summaries
    )
    assert "test_chain_metrics" not in evals.last_metrics_summaries

    # 4. Passing -n / --dist in pytest_args logs a warning about in-process collection
    with caplog.at_level("WARNING"):
        CallbackEvals._warn_if_xdist_args(["-n", "2"])
    assert any(
        "do not support pytest-xdist workers" in rec.message
        for rec in caplog.records
    )

    # 5. Multiple callback directories under same agent/callback_type do not collide
    for cb_dir_name in ("cb01", "cb02"):
        cb_dir = (
            app_dir
            / "agents"
            / "agent_a"
            / "after_model_callbacks"
            / cb_dir_name
        )
        cb_dir.mkdir(parents=True)
        (cb_dir / "python_code.py").write_text(
            "def after_model_callback(callback_context, llm_response):\n"
            "    return None\n",
            encoding="utf-8",
        )
        (cb_dir / "test.py").write_text(
            "def test_returns_none():\n"
            "    assert after_model_callback(None, None) is None\n",
            encoding="utf-8",
        )
    df_cbs = evals.test_all_callbacks_in_app_dir(
        str(app_dir),
        agent_name="agent_a",
        include_chain=False,
        include_metrics=True,
    )
    assert len(df_cbs) == 2
    assert list(df_cbs["callback_name"]) == ["cb01", "cb02"]
    assert set(evals.last_metrics_summaries.keys()) == {
        "agent_a/after_model_callbacks/cb01/test_returns_none",
        "agent_a/after_model_callbacks/cb02/test_returns_none",
    }

    # 6. ThreadPoolExecutor worker thread turns are captured by fallback collector
    (app_dir / "agents" / "agent_a" / "test_chain.py").write_text(
        "from concurrent.futures import ThreadPoolExecutor\n"
        "from python_code import SessionSimulator, runner\n"
        "from cxas_scrapi.utils.callback_libs import LlmResponse, Part, UsageMetadata\n"
        "def _run_one():\n"
        "    sim = SessionSimulator(runner)\n"
        "    sim.turn(\n"
        "        'hi',\n"
        "        mock_llm=LlmResponse.from_parts(\n"
        "            [Part.from_text('ok')],\n"
        "            usage_metadata=UsageMetadata(\n"
        "                prompt_token_count=7, candidates_token_count=0\n"
        "            ),\n"
        "        ),\n"
        "    )\n"
        "def test_main_thread():\n"
        "    _run_one()\n"
        "def test_worker_thread():\n"
        "    with ThreadPoolExecutor(1) as ex:\n"
        "        ex.submit(_run_one).result()\n",
        encoding="utf-8",
    )
    df_threads = evals.test_all_callbacks_in_app_dir(
        str(app_dir),
        agent_name="agent_a",
        chain_only=True,
        include_metrics=True,
    )
    assert len(df_threads) == 2
    main_sum = evals.last_metrics_summaries[
        "agent_a/chain_callbacks/test_main_thread"
    ]
    worker_sum = evals.last_metrics_summaries[
        "agent_a/chain_callbacks/test_worker_thread"
    ]
    assert (main_sum.turn_count, main_sum.total_tokens) == (1, 7)
    assert (worker_sum.turn_count, worker_sum.total_tokens) == (1, 7)


def test_caller_ctx_image_version_restoration_on_v2_runner() -> None:
    """Verifies caller ctx._image_version restoration after run_stage on a v2 runner."""
    from cxas_scrapi.evals.callback_chain import (  # noqa: PLC0415
        CallbackChainRunner,
    )
    from cxas_scrapi.utils.callback_libs import (  # noqa: PLC0415
        CallbackContext,
        Content,
        Event,
        Part,
    )

    events = [
        Event(
            author="user",
            content=Content(role="user", parts=[Part.from_text("t1")]),
        ),
        Event(author="root_agent", content=None),
        Event(
            author="root_agent",
            content=Content(role="model", parts=[Part.from_text("answer1")]),
        ),
        Event(
            author="user",
            content=Content(role="user", parts=[Part.from_text("t2")]),
        ),
        Event(author="root_agent", content=None),
    ]
    code = (
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['n'] = len(\n"
        "        callback_context.get_last_agent_output()\n"
        "    )\n"
    )
    v2_runner = CallbackChainRunner.from_callbacks(
        {"before_agent": [("cb", code)]},
        image_version="v2",
        new_session=False,
    )
    user_ctx = CallbackContext(state={}, events=events, image_version="v1")
    _, trace = v2_runner.run_stage("before_agent", user_ctx)
    assert trace.final_state["n"] == 0.0
    assert user_ctx._image_version == "v1"
