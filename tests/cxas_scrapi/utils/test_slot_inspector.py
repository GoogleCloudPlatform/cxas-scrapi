import json

import pytest

from cxas_scrapi.utils.slot_inspector import SLOT_CATEGORIES, SlotInspector


class TestInspect:
    def test_inspect_empty(self):
        result = SlotInspector.inspect({})
        assert "summary" in result
        assert "categories" in result
        assert "slot_dag" in result
        assert result["summary"]["phase"] == "collection"
        assert result["summary"]["status"] is None
        assert result["summary"]["filled_count"] == 0
        assert result["summary"]["pending_count"] == 0
        assert result["summary"]["deferred_count"] == 0

    def test_inspect_core_data(self):
        sm = {
            "filled": {"party_size": "4", "date": "tomorrow"},
            "pending": {"time": "7pm"},
            "deferred": {"name": "John"},
            "status": "in_progress",
        }
        result = SlotInspector.inspect(sm)
        cats = result["categories"]
        assert "core_data" in cats
        assert cats["core_data"]["label"] == "Core Data"
        assert cats["core_data"]["fields"]["filled"] == {"party_size": "4", "date": "tomorrow"}
        assert cats["core_data"]["fields"]["pending"] == {"time": "7pm"}
        assert cats["core_data"]["fields"]["deferred"] == {"name": "John"}
        assert cats["core_data"]["fields"]["status"] == "in_progress"

    def test_inspect_all_categories(self):
        sm = {}
        for cat_def in SLOT_CATEGORIES.values():
            for field in cat_def["fields"]:
                sm[field] = f"value_{field}"

        result = SlotInspector.inspect(sm)
        cats = result["categories"]
        for cat_key, cat_def in SLOT_CATEGORIES.items():
            assert cat_key in cats, f"Missing category: {cat_key}"
            assert cats[cat_key]["label"] == cat_def["label"]
            for field in cat_def["fields"]:
                assert field in cats[cat_key]["fields"]

        assert "uncategorized" not in cats

    def test_inspect_uncategorized(self):
        sm = {
            "filled": {},
            "custom_field": "abc",
            "_unknown_internal": 42,
        }
        result = SlotInspector.inspect(sm)
        cats = result["categories"]
        assert "uncategorized" in cats
        assert cats["uncategorized"]["fields"]["custom_field"] == "abc"
        assert cats["uncategorized"]["fields"]["_unknown_internal"] == 42

    def test_inspect_summary_counts(self):
        sm = {
            "filled": {"a": 1, "b": 2, "c": 3},
            "pending": {"d": 4},
            "deferred": {"e": 5, "f": 6},
        }
        result = SlotInspector.inspect(sm)
        assert result["summary"]["filled_count"] == 3
        assert result["summary"]["pending_count"] == 1
        assert result["summary"]["deferred_count"] == 2

    def test_inspect_json_serializable(self):
        sm = {
            "filled": {"party_size": "4"},
            "pending": {},
            "status": "in_progress",
            "_retries": {"lookup": 1},
            "_steer_back_turns": 2,
            "_config_id": "reservation",
        }
        result = SlotInspector.inspect(sm)
        serialized = json.dumps(result, default=str)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["summary"]["config_id"] == "reservation"


class TestDerivePhase:
    def test_phase_collection(self):
        sm = {"filled": {"a": 1}, "pending": {}, "_initialized": True}
        result = SlotInspector.inspect(sm)
        assert result["summary"]["phase"] == "collection"

    def test_phase_fresh_readback(self):
        sm = {"pending": {"party_size": "4"}}
        result = SlotInspector.inspect(sm)
        assert result["summary"]["phase"] == "fresh_readback"

    def test_phase_awaiting_confirmation(self):
        sm = {
            "_auto_confirm_pending": True,
            "pending": {"party_size": "4"},
        }
        result = SlotInspector.inspect(sm)
        assert result["summary"]["phase"] == "awaiting_confirmation"

    def test_phase_correction(self):
        sm = {"_correction_pending": True}
        result = SlotInspector.inspect(sm)
        assert result["summary"]["phase"] == "correction"

    def test_phase_complete(self):
        sm = {"status": "complete"}
        result = SlotInspector.inspect(sm)
        assert result["summary"]["phase"] == "complete"

    def test_phase_escalated(self):
        sm = {"status": "escalated"}
        result = SlotInspector.inspect(sm)
        assert result["summary"]["phase"] == "escalated"

    def test_phase_uninitialized(self):
        sm = {"_initialized": False}
        result = SlotInspector.inspect(sm)
        assert result["summary"]["phase"] == "uninitialized"


class TestBuildSlotDag:
    def test_dag_basic(self):
        sm = {
            "filled": {"party_size": "4", "date": "tomorrow"},
            "pending": {"time": "7pm"},
            "deferred": {"name": "John"},
        }
        result = SlotInspector.inspect(sm)
        dag = result["slot_dag"]
        assert dag["filled"] == ["date", "party_size"]
        assert dag["pending"] == ["time"]
        assert dag["deferred"] == ["name"]
        assert dag["blocked"] == []

    def test_dag_blocked(self):
        sm = {
            "filled": {"party_size": "4"},
            "pending": {},
            "deferred": {},
            "_slot_requires": {
                "time": ["date"],
                "confirmation": ["party_size", "date", "time"],
            },
        }
        result = SlotInspector.inspect(sm)
        dag = result["slot_dag"]
        blocked = dag["blocked"]
        assert len(blocked) == 2
        time_blocked = next(b for b in blocked if b["slot"] == "time")
        assert time_blocked["blocked_by"] == ["date"]
        conf_blocked = next(b for b in blocked if b["slot"] == "confirmation")
        assert "date" in conf_blocked["blocked_by"]
        assert "time" in conf_blocked["blocked_by"]

    def test_dag_meta_slots_from_bootstrap(self):
        sm = {
            "filled": {"active_flow": "reservation", "welcome": True,
                       "party_size": "4"},
            "pending": {},
            "_bootstrap": {"slot": "active_flow", "welcome_slot": "welcome"},
            "_gate_slot": "active_flow",
        }
        result = SlotInspector.inspect(sm)
        dag = result["slot_dag"]
        assert "active_flow" in dag["meta_slots"]
        assert "welcome" in dag["meta_slots"]
        assert "party_size" not in dag["meta_slots"]

    def test_dag_flow_slots_from_setters(self):
        sm = {
            "filled": {},
            "pending": {},
            "_setter_slots": {"set_time": "selected_time"},
            "_multi_setter_slots": {
                "set_basics": {"ps": "party_size", "d": "date"},
            },
            "_slot_ordering": "party_size → date → selected_time → name",
        }
        result = SlotInspector.inspect(sm)
        dag = result["slot_dag"]
        assert "selected_time" in dag["flow_slots"]
        assert "party_size" in dag["flow_slots"]
        assert "date" in dag["flow_slots"]
        assert "name" in dag["flow_slots"]

    def test_dag_no_blocked_when_filled(self):
        sm = {
            "filled": {"party_size": "4", "date": "tomorrow"},
            "pending": {},
            "deferred": {},
            "_slot_requires": {
                "time": ["party_size", "date"],
            },
        }
        result = SlotInspector.inspect(sm)
        dag = result["slot_dag"]
        assert dag["blocked"] == []


    def test_dag_shared_slots(self):
        sm = {
            "filled": {"guest_name": "Alice", "party_size": "4"},
            "pending": {},
            "_shared_slots": ["guest_name", "welcome"],
        }
        result = SlotInspector.inspect(sm)
        dag = result["slot_dag"]
        assert "guest_name" in dag["shared_slots"]
        assert "welcome" in dag["shared_slots"]
        assert "party_size" not in dag["shared_slots"]


class TestSuspendedFlows:
    def test_no_suspended_flows(self):
        result = SlotInspector.inspect({"filled": {}, "pending": {}})
        assert result["summary"]["suspended_flows"] == []
        assert result["summary"]["restored_flow"] is False
        assert result["summary"]["auto_resume_deferred"] is False

    def test_suspended_flow_summary(self):
        sm = {
            "filled": {"active_flow": "takeout", "party_size": "2"},
            "pending": {},
            "_flow_state": [
                {
                    "id": 1,
                    "flow": "reservation",
                    "slots": {"date": "Friday", "time": "7pm"},
                    "deferred": {"guest_name": "Alice"},
                    "task_results": {},
                },
            ],
        }
        result = SlotInspector.inspect(sm)
        suspended = result["summary"]["suspended_flows"]
        assert len(suspended) == 1
        assert suspended[0]["flow"] == "reservation"
        assert "date" in suspended[0]["slots"]
        assert "time" in suspended[0]["slots"]
        assert "guest_name" in suspended[0]["deferred"]

    def test_restored_flow_flag(self):
        sm = {
            "filled": {"active_flow": "reservation"},
            "pending": {"date": "Friday"},
            "_restored_flow": True,
        }
        result = SlotInspector.inspect(sm)
        assert result["summary"]["restored_flow"] is True

    def test_auto_resume_deferred_flag(self):
        sm = {
            "status": "complete",
            "_auto_resume_deferred": True,
            "_flow_state": [
                {"id": 1, "flow": "reservation", "slots": {}, "deferred": {}}
            ],
        }
        result = SlotInspector.inspect(sm)
        assert result["summary"]["auto_resume_deferred"] is True

    def test_multiple_suspended_flows(self):
        sm = {
            "filled": {"active_flow": "catering"},
            "pending": {},
            "_flow_state": [
                {"id": 1, "flow": "reservation", "slots": {"date": "Fri"}, "deferred": {}},
                {"id": 2, "flow": "takeout", "slots": {"pickup_time": "6pm"}, "deferred": {}},
            ],
        }
        result = SlotInspector.inspect(sm)
        suspended = result["summary"]["suspended_flows"]
        assert len(suspended) == 2
        assert suspended[0]["flow"] == "reservation"
        assert suspended[1]["flow"] == "takeout"


class TestInspectFromTrace:
    def test_from_trace_variable_update(self):
        trace = {
            "entries": [
                {
                    "kind": "variable_update",
                    "turn": 0,
                    "variables": {
                        "slot_machine": {
                            "filled": {"party_size": "4"},
                            "pending": {},
                            "status": "in_progress",
                        }
                    },
                }
            ]
        }
        result = SlotInspector.inspect_from_trace(trace)
        assert result["summary"]["filled_count"] == 1
        assert result["summary"]["phase"] == "collection"

    def test_from_trace_with_turn_filter(self):
        trace = {
            "entries": [
                {
                    "kind": "variable_update",
                    "turn": 0,
                    "variables": {
                        "slot_machine": {
                            "filled": {"party_size": "4"},
                            "pending": {},
                        }
                    },
                },
                {
                    "kind": "variable_update",
                    "turn": 2,
                    "variables": {
                        "slot_machine": {
                            "filled": {"party_size": "4", "date": "tomorrow"},
                            "pending": {"time": "7pm"},
                        }
                    },
                },
            ]
        }
        result = SlotInspector.inspect_from_trace(trace, turn=1)
        assert result["summary"]["filled_count"] == 1
        assert result["summary"]["pending_count"] == 0

    def test_from_trace_no_sm(self):
        trace = {
            "entries": [
                {"kind": "agent_response", "turn": 0, "text": "hello"}
            ]
        }
        result = SlotInspector.inspect_from_trace(trace)
        assert result["summary"]["filled_count"] == 0
        assert result["summary"]["phase"] == "collection"

    def test_from_trace_multiple_updates(self):
        trace = {
            "entries": [
                {
                    "kind": "variable_update",
                    "turn": 0,
                    "variables": {
                        "slot_machine": {
                            "filled": {"party_size": "4"},
                            "pending": {},
                        }
                    },
                },
                {
                    "kind": "variable_default",
                    "turn": 1,
                    "variables": {
                        "slot_machine": {
                            "filled": {"party_size": "4", "date": "tomorrow", "time": "7pm"},
                            "pending": {},
                            "status": "complete",
                        }
                    },
                },
            ]
        }
        result = SlotInspector.inspect_from_trace(trace)
        assert result["summary"]["filled_count"] == 3
        assert result["summary"]["phase"] == "complete"
