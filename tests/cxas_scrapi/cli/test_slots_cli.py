import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.cli import slots_cli

APP = "projects/p/locations/l/apps/a"


class TestRegister:
    def test_register_smoke(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd", required=True)
        slots_cli.register(sub)
        args = parser.parse_args(
            ["slots", "inspect", "--app-name", APP, "conv-123"]
        )
        assert args.func == slots_cli.slots_inspect
        assert args.conversation_id == "conv-123"
        assert args.app_name == APP

    def test_register_with_flags(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd", required=True)
        slots_cli.register(sub)
        args = parser.parse_args(
            [
                "slots", "inspect",
                "--app-name", APP,
                "conv-456",
                "--at-turn", "3",
                "--category", "core_data",
            ]
        )
        assert args.at_turn == 3
        assert args.category == "core_data"


class TestSlotsInspect:
    @patch.object(slots_cli, "_build_traces")
    def test_slots_inspect_basic(self, mock_build, capsys):
        mock_traces = MagicMock()
        mock_traces.get_normalized.return_value = {
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
        mock_build.return_value = mock_traces

        args = argparse.Namespace(
            app_name=APP, conversation_id="conv-123",
            at_turn=None, category=None,
        )
        slots_cli.slots_inspect(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["summary"]["filled_count"] == 1
        assert output["summary"]["phase"] == "collection"

    @patch.object(slots_cli, "_build_traces")
    def test_slots_inspect_with_turn(self, mock_build, capsys):
        mock_traces = MagicMock()
        mock_traces.get_normalized.return_value = {
            "entries": [
                {
                    "kind": "variable_update",
                    "turn": 0,
                    "variables": {
                        "slot_machine": {
                            "filled": {"a": "1"},
                            "pending": {},
                        }
                    },
                },
                {
                    "kind": "variable_update",
                    "turn": 3,
                    "variables": {
                        "slot_machine": {
                            "filled": {"a": "1", "b": "2", "c": "3"},
                            "pending": {},
                        }
                    },
                },
            ]
        }
        mock_build.return_value = mock_traces

        args = argparse.Namespace(
            app_name=APP, conversation_id="conv-123",
            at_turn=1, category=None,
        )
        slots_cli.slots_inspect(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["summary"]["filled_count"] == 1

    @patch.object(slots_cli, "_build_traces")
    def test_slots_inspect_with_category(self, mock_build, capsys):
        mock_traces = MagicMock()
        mock_traces.get_normalized.return_value = {
            "entries": [
                {
                    "kind": "variable_update",
                    "turn": 0,
                    "variables": {
                        "slot_machine": {
                            "filled": {"party_size": "4"},
                            "pending": {"time": "7pm"},
                            "status": "in_progress",
                            "_config_id": "reservation",
                        }
                    },
                }
            ]
        }
        mock_build.return_value = mock_traces

        args = argparse.Namespace(
            app_name=APP, conversation_id="conv-123",
            at_turn=None, category="core_data",
        )
        slots_cli.slots_inspect(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "core_data" in output["categories"]
        assert len(output["categories"]) == 1

    @patch.object(slots_cli, "_build_traces")
    def test_slots_inspect_failure(self, mock_build, capsys):
        mock_build.side_effect = RuntimeError("connection refused")

        args = argparse.Namespace(
            app_name=APP, conversation_id="conv-123",
            at_turn=None, category=None,
        )
        with pytest.raises(SystemExit) as exc:
            slots_cli.slots_inspect(args)
        assert exc.value.code == 1
        assert "connection refused" in capsys.readouterr().err
