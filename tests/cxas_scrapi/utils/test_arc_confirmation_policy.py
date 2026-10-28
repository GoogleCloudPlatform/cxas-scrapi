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

"""Tests that policies answer cards like the corresponding Console gestures."""

from __future__ import annotations

import dataclasses

import pytest

from cxas_scrapi.utils import arc_confirmation_policy as policy_lib

_Action = policy_lib.ConfirmationActionType


# Minimal stand-ins for the Arc client's record types; the policy module only
# relies on the attributes below (see `CardLike`).
@dataclasses.dataclass
class _Choice:
    title: str = ""
    submit_text: str = ""


@dataclasses.dataclass
class _Question:
    question: str = ""
    options: list[_Choice] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class _Card:
    confirmation_id: str = ""
    tool: str = ""
    context: str = ""
    questions: list[_Question] = dataclasses.field(default_factory=list)
    positive_label: str = ""
    negative_label: str = ""


def _gate(cid: str = "c1", tool: str = "update_app") -> _Card:
    return _Card(
        confirmation_id=cid,
        tool=tool,
        positive_label="Publish",
        negative_label="Not yet",
    )


def _question(cid: str = "q1") -> _Card:
    return _Card(
        confirmation_id=cid,
        tool="ask_user",
        questions=[
            _Question(
                question="Which channel?",
                options=[
                    _Choice(title="Voice", submit_text="Voice only"),
                    _Choice(title="Chat"),
                ],
            ),
            _Question(question="Escalate?", options=[]),
        ],
    )


def test_manual_answers_nothing() -> None:
    policy = policy_lib.make_policy("manual")
    assert not policy.answers_cards
    assert policy.decide([_gate(), _question()]) == []


def test_format_question_answer_matches_console() -> None:
    text = policy_lib.format_question_answer(
        _question().questions, ["Voice only", None]
    )
    assert text == "> Which channel?\nVoice only\n\n> Escalate?\n(skipped)"


def test_is_question_card_recognizes_blank_ask_user_card() -> None:
    blank_ask_user = _Card(
        confirmation_id="q0",
        tool="ces_assistant_ask_user",
        questions=[],
    )
    assert policy_lib.is_question_card(blank_ask_user)
    assert (
        policy_lib.first_option_answer(blank_ask_user)
        == policy_lib.SKIP_ROUND_ANSWER
    )


def test_click_confirms_gates_and_answers_questions() -> None:
    actions = policy_lib.ClickPolicy().decide([_gate(), _question()])
    assert [a.action for a in actions] == [
        _Action.CLICK_CONFIRM,
        _Action.CLICK_ANSWER,
    ]
    assert actions[0].confirmed is True
    assert (
        actions[1].text
        == "> Which channel?\nVoice only\n\n> Escalate?\n(skipped)"
    )
    assert not actions[1].is_typed


def test_click_uses_question_answerer() -> None:
    policy = policy_lib.ClickPolicy(
        question_answerer=lambda card: f"from responder {card.confirmation_id}"
    )
    assert policy.decide([_question()])[0].text == "from responder q1"


def test_click_explicit_answers_override_question_answerer() -> None:
    policy = policy_lib.ClickPolicy(
        answers={"ask_user": "explicit"},
        question_answerer=lambda card: "ignored",
    )
    assert policy.decide([_question()])[0].text == "explicit"


def test_click_falls_back_when_question_answerer_raises() -> None:
    def _boom(unused_card: policy_lib.CardLike) -> str | None:
        raise RuntimeError("responder failed")

    policy = policy_lib.ClickPolicy(question_answerer=_boom)
    action = policy.decide([_question()])[0]
    assert action.reason == "first_option"
    assert "Voice only" in action.text


def test_click_deny_tools() -> None:
    policy = policy_lib.ClickPolicy(deny_tools=["deploy*"])
    actions = policy.decide([_gate(tool="deploy_app"), _gate("c2")])
    assert [a.action for a in actions] == [
        _Action.CLICK_DENY,
        _Action.CLICK_CONFIRM,
    ]
    assert actions[0].confirmed is False


def test_click_skips_cards_without_id() -> None:
    assert policy_lib.ClickPolicy().decide([_gate(cid="")]) == []


def test_mixed_is_deterministic_for_seed() -> None:
    cards = [_gate(f"c{i}") for i in range(50)]
    first = policy_lib.MixedPolicy(typed_reply_probability=0.5, seed=7)
    second = policy_lib.MixedPolicy(typed_reply_probability=0.5, seed=7)
    a1 = [a.action for a in first.decide(cards)]
    a2 = [a.action for a in second.decide(cards)]
    assert a1 == a2
    assert _Action.TYPE_TEXT in a1
    assert _Action.CLICK_CONFIRM in a1
    assert "seed=7" in first.describe()


def test_mixed_default_seed_and_typed_deny() -> None:
    policy = policy_lib.MixedPolicy(
        typed_reply_probability=1.0, deny_tools=["deploy*"]
    )
    assert policy.seed >= 0
    assert policy.seed < 2**31
    action = policy.decide([_gate(tool="deploy_app")])[0]
    assert action.action == _Action.TYPE_TEXT
    assert action.text == policy_lib.DEFAULT_TYPED_DENY_TEXT


@pytest.mark.parametrize(
    ("prob", "expected_action", "expected_text"),
    [
        (1.0, _Action.TYPE_TEXT, "Yes"),
        (0.0, _Action.CLICK_CONFIRM, ""),
    ],
)
def test_mixed_extremes(
    prob: float, expected_action: _Action, expected_text: str
) -> None:
    action = policy_lib.MixedPolicy(
        typed_reply_probability=prob, seed=1
    ).decide([_gate()])[0]
    assert action.action == expected_action
    assert action.text == expected_text


def test_mixed_typed_question_card_types_the_answer() -> None:
    action = policy_lib.MixedPolicy(typed_reply_probability=1.0, seed=1).decide(
        [_question()]
    )[0]
    assert action.action == _Action.TYPE_TEXT
    assert "Voice only" in action.text
    assert action.confirmed is None


def test_mixed_rejects_bad_probability() -> None:
    with pytest.raises(ValueError, match="typed_reply_probability"):
        policy_lib.MixedPolicy(typed_reply_probability=1.5)


def test_scripted_rules_and_fallback() -> None:
    policy = policy_lib.ScriptedPolicy.from_yaml("""
confirmation_script:
  rules:
    - {tool: "deploy*", action: type, text: "yes"}
    - {kind: question, action: skip_all}
    - {tool: update_app, occurrence: 2, action: deny}
""")
    actions = policy.decide(
        [
            _gate("d1", tool="deploy_app"),
            _question("q1"),
            _gate("u1"),
            _gate("u2"),
        ]
    )
    assert [(a.confirmation_id, a.action) for a in actions] == [
        ("d1", _Action.TYPE_TEXT),
        ("q1", _Action.CLICK_ANSWER),
        ("u1", _Action.CLICK_CONFIRM),  # Fallback: occurrence 1 != 2.
        ("u2", _Action.CLICK_DENY),
    ]
    assert actions[0].text == "yes"
    assert actions[0].reason == "rule action=type tool=deploy*"
    assert actions[1].text == policy_lib.SKIP_ALL_ANSWER
    assert actions[1].reason == "rule action=skip_all tool=*"
    assert "scripted(rules=3, default=click" in policy.describe()


def test_scripted_context_regex_skip_round_and_reset() -> None:
    rule = policy_lib.ScriptRule(
        action="skip_round",
        context_regex="baggage",
        kind="question",
        max_uses=1,
    )
    policy = policy_lib.ScriptedPolicy(rules=[rule], default="manual")
    q_match = _question("q1")
    q_match.context = "Clarify baggage policy"
    q_miss = _question("q2")
    q_miss.context = "Clarify refund policy"
    assert policy.decide([q_miss]) == []
    actions = policy.decide([q_match])
    assert len(actions) == 1
    assert actions[0].text == policy_lib.SKIP_ROUND_ANSWER
    assert policy.decide([q_match]) == []
    # Caller's rule instance was not mutated, and reset() restores the policy.
    assert rule._used == 0
    policy.reset()
    assert len(policy.decide([q_match])) == 1


def test_scripted_manual_default_and_max_uses() -> None:
    policy = policy_lib.ScriptedPolicy.from_dict(
        {
            "default": "manual",
            "rules": [{"action": "confirm", "max_uses": 1}],
        }
    )
    actions = policy.decide([_gate("a"), _gate("b")])
    assert [a.confirmation_id for a in actions] == ["a"]


def test_scripted_typed_fallback_probability_and_seed() -> None:
    policy = policy_lib.ScriptedPolicy.from_dict(
        {
            "typed_reply_probability": 1.0,
            "seed": 42,
            "typed_confirm_text": "go ahead",
        }
    )
    action = policy.decide([_gate()])[0]
    assert action.action == _Action.TYPE_TEXT
    assert action.text == "go ahead"
    assert "seed=42" in policy.describe()


def test_scripted_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="Unknown confirmation script keys"):
        policy_lib.ScriptedPolicy.from_dict({"bogus": 1})


@pytest.mark.parametrize(
    ("script", "match"),
    [
        ({"rules": [{"action": "explode"}]}, "Unknown scripted action"),
        (
            {"rules": [{"action": "confirm", "kind": "mystery"}]},
            "Unknown card kind",
        ),
    ],
)
def test_scripted_rejects_unknown_actions_and_kinds(
    script: dict, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        policy_lib.ScriptedPolicy.from_dict(script)


def test_scripted_rejects_unknown_default() -> None:
    with pytest.raises(ValueError, match="Unknown scripted default"):
        policy_lib.ScriptedPolicy(default="yolo")


def test_make_policy_variants_and_unknown() -> None:
    mixed = policy_lib.make_policy("mixed", typed_reply_probability=1.0, seed=9)
    assert mixed.decide([_gate()])[0].action == _Action.TYPE_TEXT
    scripted = policy_lib.make_policy(
        "scripted",
        typed_reply_probability=1.0,
        seed=11,
        answers={"ask_user": "from_make_policy"},
        script={"rules": [{"tool": "ask_user", "action": "answer"}]},
    )
    assert scripted.decide([_question()])[0].text == "from_make_policy"
    assert "seed=11" in scripted.describe()
    with pytest.raises(ValueError, match="Unknown confirmation policy"):
        policy_lib.make_policy("yolo")
