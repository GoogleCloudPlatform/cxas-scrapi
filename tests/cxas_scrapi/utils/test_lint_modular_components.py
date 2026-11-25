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

"""Unit tests for modular component lint rules (I021-I026, C020)."""

import json
from pathlib import Path

from cxas_scrapi.utils.lint_rules.modular_components import (
    BeforeAgentMissingTurnGuard,
    ModularCallbackTimingRace,
    ModularChannelModalityTag,
    ModularHyphenatedVarInExpr,
    ModularPrefixCacheHazard,
    ModularUnmaskedModalityTool,
    ModularUnsupportedJinjaSubset,
)
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    Severity,
    build_context,
    build_registry,
    run_rules,
)


def _ctx(tmp_path: Path, options: dict | None = None) -> LintContext:
    return LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
        app_root=tmp_path,
        options=options or {},
    )


def _write_agent_inst(tmp_path: Path, agent: str, text: str) -> Path:
    inst = tmp_path / "agents" / agent / "instruction.txt"
    inst.parent.mkdir(parents=True, exist_ok=True)
    inst.write_text(text, encoding="utf-8")
    return inst


# ── Registry tests ───────────────────────────────────────────────────────


def test_modular_rules_registered_in_registry() -> None:
    registry = build_registry()
    rule_ids = {r.id for r in registry.all_rules()}
    expected_inst = {"I021", "I022", "I023", "I024", "I025", "I026"}
    assert expected_inst.issubset(rule_ids)
    assert "C020" in rule_ids
    for rid in expected_inst:
        rule_obj = registry.get(rid)
        assert rule_obj is not None
        assert rule_obj.category == "instructions"
        assert rule_obj.default_severity == Severity.WARNING
    c020 = registry.get("C020")
    assert c020 is not None
    assert c020.category == "callbacks"
    assert c020.default_severity == Severity.WARNING


# ── I021: Callback timing race ───────────────────────────────────────────


def test_i021_flags_before_model_write_flat_and_nested_layouts(
    tmp_path: Path,
) -> None:
    # Nested CES export layout: before_model_callbacks/<name>/python_code.py
    agent_dir = tmp_path / "agents" / "returns_agent"
    nested_cb = (
        agent_dir / "before_model_callbacks" / "cb_01" / "python_code.py"
    )
    nested_cb.parent.mkdir(parents=True)
    nested_cb.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.state['can_return_free'] = True\n"
        "    callback_context.state['retry_count'] = 1\n"
        "    return None\n"
    )
    inst = _write_agent_inst(
        tmp_path,
        "returns_agent",
        "{% if can_return_free %}Offer free return.{% endif %}\n"
        "Attempts: {{ retry_count }}\n",
    )
    res = ModularCallbackTimingRace().check(
        inst, inst.read_text(), _ctx(tmp_path)
    )
    assert len(res) == 2
    assert all(r.rule_id == "I021" for r in res)
    assert all(r.severity == Severity.WARNING for r in res)
    if_res = next(r for r in res if "can_return_free" in r.message)
    print_res = next(r for r in res if "retry_count" in r.message)
    assert "{can_return_free}" not in if_res.fix_suggestion
    assert "<state_update>" in print_res.fix_suggestion
    assert any("retry_count" in r.message for r in res)


def test_i021_detects_legacy_prefix_callback_dir(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents" / "legacy_agent"
    legacy_cb = agent_dir / "before_model_callbacks_01" / "python_code.py"
    legacy_cb.parent.mkdir(parents=True)
    legacy_cb.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.set_variable('tier_flag', 'gold')\n"
        "    return None\n"
    )
    inst = _write_agent_inst(
        tmp_path,
        "legacy_agent",
        "{{% if tier_flag == 'gold' %}}VIP{{% endif %}}\n",
    )
    res = ModularCallbackTimingRace().check(
        inst, inst.read_text(), _ctx(tmp_path)
    )
    assert len(res) == 1
    assert "tier_flag" in res[0].message


def test_i021_passes_when_written_in_before_agent(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents" / "good_agent"
    bm = agent_dir / "before_model_callbacks" / "cb_01" / "python_code.py"
    ba = agent_dir / "before_agent_callbacks" / "init_01" / "python_code.py"
    bm.parent.mkdir(parents=True)
    ba.parent.mkdir(parents=True)
    ba.write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['can_return_free'] = True\n"
    )
    bm.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    callback_context.state['can_return_free'] = False\n"
    )
    inst = _write_agent_inst(
        tmp_path,
        "good_agent",
        "{% if can_return_free %}Eligible.{% endif %}\n",
    )
    assert (
        ModularCallbackTimingRace().check(
            inst, inst.read_text(), _ctx(tmp_path)
        )
        == []
    )


def test_i021_ignores_global_instruction(tmp_path: Path) -> None:
    g = tmp_path / "global_instruction.txt"
    g.write_text("Today is {{ current_date }}.\n")
    assert (
        ModularCallbackTimingRace().check(g, g.read_text(), _ctx(tmp_path))
        == []
    )


# ── I022: Prefix-cache hazard ────────────────────────────────────────────


def test_i022_warns_on_volatile_var_near_top_of_long_prompt(
    tmp_path: Path,
) -> None:
    tool_py = (
        tmp_path
        / "tools"
        / "check_order"
        / "python_function"
        / "python_code.py"
    )
    tool_py.parent.mkdir(parents=True)
    tool_py.write_text(
        "def check_order(order_id: str) -> dict:\n"
        "    context.state['cart_count'] = 2\n"
        "    context.state['loyalty_mode'] = 'vip'\n"
        "    return {'ok': True}\n"
    )
    filler = (
        "Static policy sentence covering shipping and billing guidelines. " * 60
    )
    inst = _write_agent_inst(
        tmp_path,
        "shop_agent",
        "Cart: {{ cart_count }}\n"
        "{% if loyalty_mode == 'vip' %}VIP greeting{% endif %}\n" + filler,
    )
    res = ModularPrefixCacheHazard().check(
        inst, inst.read_text(), _ctx(tmp_path)
    )
    assert len(res) == 2
    by_msg = {r.message: r for r in res}
    print_res = next(r for m, r in by_msg.items() if "cart_count" in m)
    if_res = next(r for m, r in by_msg.items() if "loyalty_mode" in m)
    assert print_res.severity == Severity.WARNING
    assert "lower" in print_res.fix_suggestion
    assert "{cart_count}" in print_res.fix_suggestion
    assert "<state_update>" in print_res.fix_suggestion
    assert "bottom" in if_res.fix_suggestion


def test_i022_exempts_immutable_and_guarded_and_bottom_vars(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / "agents" / "clean_agent"
    ba = agent_dir / "before_agent_callbacks" / "init" / "python_code.py"
    ba.parent.mkdir(parents=True)
    ba.write_text(
        "def before_agent_callback(callback_context):\n"
        "    if not callback_context.state.get('initialized'):\n"
        "        callback_context.state['feature_flags'] = 'v2'\n"
        "    if callback_context.state.get('played_greeting'):\n"
        "        return None\n"
        "    callback_context.state['played_greeting'] = True\n"
        "    callback_context.state['late_counter'] = 5\n"
    )
    filler = "Static policy sentence repeated for prompt prefix caching. " * 60
    inst = _write_agent_inst(
        tmp_path,
        "clean_agent",
        "Date: {{ current_date }}\n"
        "Brand: {{ brand }}\n"
        "Flag: {{ feature_flags }}\n"
        "Greeted: {{ played_greeting }}\n"
        + filler
        + "\nEnd counter: {{ late_counter }}\n",
    )
    assert (
        ModularPrefixCacheHazard().check(inst, inst.read_text(), _ctx(tmp_path))
        == []
    )


def test_i022_respects_custom_immutable_keys_option(tmp_path: Path) -> None:
    tool_py = tmp_path / "tools" / "t" / "python_function" / "python_code.py"
    tool_py.parent.mkdir(parents=True)
    tool_py.write_text(
        "def t() -> dict:\n    state['tenant_id'] = 'tenant_a'\n    return {}\n"
    )
    filler = "Static policy line repeated for cache window check. " * 60
    inst = _write_agent_inst(
        tmp_path, "a", "Tenant: {{ tenant_id }}\n" + filler
    )
    ctx = _ctx(tmp_path, options={"I022": {"immutable_keys": ["tenant_id"]}})
    assert ModularPrefixCacheHazard().check(inst, inst.read_text(), ctx) == []


def test_i022_exempts_session_wide_early_return_init_guard(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / "agents" / "guarded_agent"
    ba = agent_dir / "before_agent_callbacks" / "cb_01" / "python_code.py"
    ba.parent.mkdir(parents=True)
    ba.write_text(
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    if state.get('_initialized') == 'true':\n"
        "        return None\n"
        "    state['_initialized'] = 'true'\n"
        "    state['verified_account_id'] = 'ACC-123'\n"
        "    state['account_summary'] = 'Standard Tier'\n"
        "    return None\n"
    )
    filler = "Static policy sentence repeated for prompt prefix caching. " * 60
    inst = _write_agent_inst(
        tmp_path,
        "guarded_agent",
        "Account: {{ verified_account_id }} ({{ account_summary }})\n" + filler,
    )
    assert (
        ModularPrefixCacheHazard().check(inst, inst.read_text(), _ctx(tmp_path))
        == []
    )


# ── I023: Unsupported CES Jinja subset ───────────────────────────────────


def test_i023_flags_filters_loop_attrs_funcs_tags_braces_and_balance(
    tmp_path: Path,
) -> None:
    inst = _write_agent_inst(
        tmp_path,
        "bad_jinja",
        "{{ user_name | lower }}\n"
        "{{ items | join(', ') }}\n"
        "{% set total = 1 %}\n"
        "{% if {balance} > 0 %}\n"
        "  {{ unknown_helper(balance) }}\n"
        "  {% for item in items %}\n"
        "    {{ loop.index }} {{ loop.first }}\n"
        "  {% endfor %}\n",
    )
    res = ModularUnsupportedJinjaSubset().check(
        inst, inst.read_text(), _ctx(tmp_path)
    )
    msgs = "\n".join(r.message for r in res)
    assert "| lower" in msgs
    assert "| join" in msgs
    assert "{% set ... %}" in msgs
    assert "Nested curly braces" in msgs
    assert "unknown_helper(...)" in msgs
    assert "loop.index" in msgs
    assert "loop.first" in msgs
    assert "Unclosed '{% if ... %}'" in msgs


def test_i023_allows_supported_subset_macros_dynamic_blocks_and_exemptions(
    tmp_path: Path,
) -> None:
    inst = _write_agent_inst(
        tmp_path,
        "good_jinja",
        "{% macro badge(name) %}[{{ name | upper }}]{% endmacro %}\n"
        "{{ badge(tier) }}\n"
        "{{% if items | length > 0 %}}\n"
        "  {% for item in items %}{{ item }}{% if not loop.last %}, {% endif %}{% endfor %}\n"
        "{{% elif in_time_window('09:00', '17:00') %}}\n"
        "  Open: {{ label | default('N/A') }}\n"
        "{{% else %}}\n"
        "  Closed\n"
        "{{% endif %}}\n"
        "DTMF: {{#1234#}}\n"
        "<inline_example>\n"
        "{{ ignored | title }} {{ loop.index }}\n"
        "</inline_example>\n"
        "```jinja\n"
        "{% set doc_only = 1 %}\n"
        "```\n",
    )
    assert (
        ModularUnsupportedJinjaSubset().check(
            inst, inst.read_text(), _ctx(tmp_path)
        )
        == []
    )


def test_i023_supports_else_if_branch_and_no_double_report_on_comments(
    tmp_path: Path,
) -> None:
    inst = _write_agent_inst(
        tmp_path,
        "else_if_agent",
        "{% if a %}A{% else if b %}B{% else %}C{% endif %}\n"
        "{{# comment handled by I020 #}}\n",
    )
    assert (
        ModularUnsupportedJinjaSubset().check(
            inst, inst.read_text(), _ctx(tmp_path)
        )
        == []
    )


# ── I024: Channel & modality tags ────────────────────────────────────────


def test_i024_catches_invalid_mismatched_unclosed_and_xml_wrap(
    tmp_path: Path,
) -> None:
    inst = _write_agent_inst(
        tmp_path,
        "tags_bad",
        "{@startModality_Audio}nope{@endModality_Audio}\n"
        "{@startChannel_UnknownChat}nope{@endChannel_UnknownChat}\n"
        "{@startModality_Voice}hi{@endModality_Text}\n"
        "{@startModalityVoice}no underscore{@endChannel_Twilio}\n"
        "{@startChannel_Twilio}\n"
        "<role>Wrapped top-level role</role>\n"
        "{@endChannel_Twilio}\n"
        "{@startChannel_API}unclosed\n",
    )
    res = ModularChannelModalityTag().check(
        inst, inst.read_text(), _ctx(tmp_path)
    )
    msgs = [r.message for r in res]
    assert any("Invalid modality tag" in m for m in msgs)
    assert any("Invalid channel tag" in m for m in msgs)
    assert any("Mismatched closer '{@endModality_Text}'" in m for m in msgs)
    assert any("Mismatched closer '{@endChannel_Twilio}'" in m for m in msgs)
    assert any("Top-level XML section '<role>'" in m for m in msgs)
    assert any("Unclosed tag '{@startChannel_API}'" in m for m in msgs)
    xml_finding = next(r for r in res if "Top-level XML section" in r.message)
    assert xml_finding.severity == Severity.WARNING


def test_i024_accepts_all_valid_modality_and_channel_aliases(
    tmp_path: Path,
) -> None:
    inst = _write_agent_inst(
        tmp_path,
        "tags_ok",
        "<role>\n"
        "{@startModality_Text}text{@endModality_TEXT}\n"
        "{@startModality_VOICE}voice{@endModality_Voice}\n"
        "{@startChannel_WebWidget}web{@endChannel_WEB_UI}\n"
        "{@startChannel_WhatsApp}wa{@endChannel_WHATSAPP}\n"
        "{@startChannel_GoogleTelephonyPlatform}gtp{@endChannel_GOOGLE_TELEPHONY_PLATFORM}\n"
        "{@startChannel_GoogleCCaaSVoice}v{@endChannel_CONTACT_CENTER_AS_A_SERVICE}\n"
        "{@startChannel_GoogleCCaaSChat}c{@endChannel_CONTACT_CENTER_AS_A_SERVICE_CHAT}\n"
        "{@startChannel_AudioCodes}a{@endChannel_CONTACT_CENTER_INTEGRATION}\n"
        "</role>\n",
    )
    assert (
        ModularChannelModalityTag().check(
            inst, inst.read_text(), _ctx(tmp_path)
        )
        == []
    )


# ── I025: Unmasked modality-scoped tools/widgets ─────────────────────────


def test_i025_warns_on_unmasked_tool_and_widget(tmp_path: Path) -> None:
    inst = _write_agent_inst(
        tmp_path,
        "unmasked_agent",
        "{@startModality_Text}\n"
        "Show {@WIDGET: order_card} or call {@TOOL: send_link}.\n"
        "{@endModality_Text}\n"
        "{@startModality_Voice}\n"
        "Speak plainly.\n"
        "{@endModality_Voice}\n",
    )
    res = ModularUnmaskedModalityTool().check(
        inst, inst.read_text(), _ctx(tmp_path)
    )
    assert len(res) == 2
    assert all(r.rule_id == "I025" for r in res)
    assert all(r.severity == Severity.WARNING for r in res)


def test_i025_passes_with_callback_mask_or_explicit_prohibition(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / "agents" / "masked_agent"
    bm = agent_dir / "before_model_callbacks" / "mask_cb" / "python_code.py"
    bm.parent.mkdir(parents=True)
    bm.write_text(
        "def before_model_callback(callback_context, llm_request):\n"
        "    mod = callback_context.get_variable('_session', {}).get('modality')\n"
        "    if mod == 'VOICE':\n"
        "        llm_request.config.hide_tool('send_link')\n"
    )
    inst = _write_agent_inst(
        tmp_path,
        "masked_agent",
        "{@startModality_Text}\n"
        "Use {@TOOL: send_link} and {@WIDGET: order_card}.\n"
        "{@endModality_Text}\n"
        "{@startModality_Voice}\n"
        "Never use the widget on voice calls.\n"
        "{@endModality_Voice}\n",
    )
    assert (
        ModularUnmaskedModalityTool().check(
            inst, inst.read_text(), _ctx(tmp_path)
        )
        == []
    )


# ── I026: Hyphenated variable names ──────────────────────────────────────


def test_i026_flags_block_and_filtered_print_allows_plain_and_quoted(
    tmp_path: Path,
) -> None:
    inst = _write_agent_inst(
        tmp_path,
        "hyphen_agent",
        "{% if user-tier == 'vip-gold' %}VIP{% endif %}\n"
        "{% else if region-code == 'us-east' %}US{% endif %}\n"
        "{{ telephony-caller-id | default('unknown') }}\n"
        "Plain OK: {{ telephony-caller-id }}\n"
        "Quoted path OK: {% if route == '/privacy-notice' %}ok{% endif %}\n"
        "Negative OK: {% if status == '-1' %}err{% endif %}\n"
        "Subtracted OK: {% if total - 1 > 0 %}pos{% endif %}\n",
    )
    res = ModularHyphenatedVarInExpr().check(
        inst, inst.read_text(), _ctx(tmp_path)
    )
    assert len(res) == 3
    assert all(r.severity == Severity.WARNING for r in res)
    msgs = [r.message for r in res]
    assert any("user-tier" in m for m in msgs)
    assert any("region-code" in m for m in msgs)
    assert any("plain '{{telephony-caller-id}}'" in m for m in msgs)


# ── C020: Missing turn guard in before_agent_callback ────────────────────


def test_c020_flags_unguarded_default_reset_literals(tmp_path: Path) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root_agent"
        / "before_agent_callbacks"
        / "cb_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "def before_agent_callback(callback_context):\n"
        "    s = callback_context.state\n"
        "    s['order_id'] = ''\n"
        "    s['retry_count'] = 0\n"
        "    s['verified'] = False\n"
        "    s['items'] = []\n"
        "    callback_context.set_variable('meta', {})\n"
        "    s.update({'flags': ''})\n"
        "    s['computed'] = compute_value(s)\n"
        "    return None\n"
    )
    res = BeforeAgentMissingTurnGuard().check(
        cb, cb.read_text(), _ctx(tmp_path)
    )
    assert len(res) == 6
    assert all(
        r.rule_id == "C020" and r.severity == Severity.WARNING for r in res
    )
    msgs = "\n".join(r.message for r in res)
    for key in (
        "order_id",
        "retry_count",
        "verified",
        "items",
        "meta",
        "flags",
    ):
        assert f"'{key}'" in msgs
    assert "computed" not in msgs


def test_c020_passes_when_guarded_or_setdefault_or_conditional(
    tmp_path: Path,
) -> None:
    cb = (
        tmp_path
        / "agents"
        / "root_agent"
        / "before_agent_callbacks"
        / "cb_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)

    # Way 2: early return guard using == 'true'
    cb.write_text(
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    if state.get('_initialized') == 'true':\n"
        "        return None\n"
        "    state['_initialized'] = 'true'\n"
        "    state['order_id'] = ''\n"
        "    state['retry_count'] = 0\n"
        "    return None\n"
    )
    assert (
        BeforeAgentMissingTurnGuard().check(cb, cb.read_text(), _ctx(tmp_path))
        == []
    )

    # Way 1: wrapped inside `if not state.get(...)` + setdefault + conditional reset
    cb.write_text(
        "def before_agent_callback(callback_context):\n"
        "    state = callback_context.state\n"
        "    state.setdefault('retry_count', 0)\n"
        "    if not state.get('_initialized'):\n"
        "        state['_initialized'] = True\n"
        "        state['order_id'] = ''\n"
        "    if state.get('reset_requested'):\n"
        "        state['retry_count'] = 0\n"
        "    return None\n"
    )
    assert (
        BeforeAgentMissingTurnGuard().check(cb, cb.read_text(), _ctx(tmp_path))
        == []
    )


def test_c020_ignores_non_before_agent_callbacks(tmp_path: Path) -> None:
    bm = (
        tmp_path
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "cb_01"
        / "python_code.py"
    )
    bm.parent.mkdir(parents=True)
    bm.write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['retry_count'] = 0\n"
    )
    assert (
        BeforeAgentMissingTurnGuard().check(bm, bm.read_text(), _ctx(tmp_path))
        == []
    )


# ── End-to-end run_rules coexistence with I020 and global_instruction ────


def test_end_to_end_run_rules_coexists_with_i020_and_global_instruction(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text(
        json.dumps({"displayName": "Demo", "rootAgent": "root_agent"})
    )
    (tmp_path / "global_instruction.txt").write_text(
        "{@startChannel_InvalidChan}x{@endChannel_InvalidChan}\n"
        "{% if 'admin' not in roles %}hi{% endif %}\n"
        "{{ user_name | capitalize }}\n"
    )
    agent_dir = tmp_path / "agents" / "root_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "root_agent.json").write_text(
        json.dumps(
            {
                "displayName": "root_agent",
                "instruction": "agents/root_agent/instruction.txt",
            }
        )
    )
    (agent_dir / "instruction.txt").write_text(
        "Today is {{ current_date }}.\n"
        "{% if foo-bar %}{{ loop.index }}{% endif %}\n"
    )
    ba = agent_dir / "before_agent_callbacks" / "init_cb" / "python_code.py"
    ba.parent.mkdir(parents=True)
    ba.write_text(
        "def before_agent_callback(callback_context):\n"
        "    callback_context.state['retry_count'] = 0\n"
    )

    registry = build_registry()
    config = LintConfig()
    discovery = Discovery(tmp_path, tmp_path / "evals")
    context = build_context(tmp_path, config, discovery)
    report = LintReport()
    run_rules(
        registry,
        config,
        context,
        discovery,
        report,
        specific_rules={
            "I020",
            "I021",
            "I022",
            "I023",
            "I024",
            "I025",
            "I026",
            "C020",
        },
    )
    found_ids = {r.rule_id for r in report.results}
    assert {"I020", "I023", "I024", "I026", "C020"}.issubset(found_ids)
    # I020 remains ERROR; all new modular rules default to WARNING.
    for r in report.results:
        if r.rule_id == "I020":
            assert r.severity == Severity.ERROR
        else:
            assert r.severity == Severity.WARNING
    assert any(
        r.file == "global_instruction.txt" and r.rule_id == "I024"
        for r in report.results
    )
    assert any(
        r.file == "global_instruction.txt" and r.rule_id == "I023"
        for r in report.results
    )
