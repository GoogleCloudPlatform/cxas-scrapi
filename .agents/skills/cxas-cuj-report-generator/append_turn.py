"""Tool for agents to incrementally build transcripts in YAML format.

Typical usage example:
    python append_turn.py --transcript_file=p.yml --input_file=t.yml
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import sys

# pylint: disable=bad-indentation
from collections.abc import Mapping, Sequence
from typing import Any

import yaml
from absl import app, flags, logging


@contextlib.contextmanager
def file_lock(lock_file_path: pathlib.Path):
    lock_file_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_file_path, "w")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_RLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()

_TRANSCRIPT_FILE = flags.DEFINE_string(
    "transcript_file",
    None,
    "Path to the target YAML transcript file",
    required=True,
)
_INPUT_FILE = flags.DEFINE_string(
    "input_file",
    None,
    "Path to the YAML file containing the turn input",
    required=True,
)


def process_append_turn(
    *, transcript_data: Mapping[str, Any], turn_input: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Processes the addition of a turn to transcript data.

    Args:
        transcript_data: Current transcript data mapping (can be empty).
        turn_input: Input turn data mapping to append.

    Returns:
        Updated transcript data mapping.

    Raises:
        ValueError: If required metadata is missing for the first turn,
          or if turn structure is invalid.
    """
    if not transcript_data:
        required_meta = ["subintent_id", "subintent_name", "parent_cuj"]
        if not all(k in turn_input for k in required_meta):
            raise ValueError(
                "First turn must include subintent_id, subintent_name,"
                " and parent_cuj in the input file"
            )
        base_data = {
            "subintent_id": turn_input["subintent_id"],
            "subintent_name": turn_input["subintent_name"],
            "parent_cuj": turn_input["parent_cuj"],
            "turns": [],
        }
    else:
        base_data = transcript_data

    if "speaker" not in turn_input or "text" not in turn_input:
        raise ValueError("Input file must contain 'speaker' and 'text'")

    text = (
        str(turn_input["text"])
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
    )

    optional_fields = {
        k: turn_input[k]
        for k in ["tool_call", "webhook_call", "system_action"]
        if k in turn_input
    }

    turn = {"speaker": turn_input["speaker"], "text": text, **optional_fields}

    return {
        **base_data,
        "turns": [*base_data.get("turns", []), turn],
    }


def main(argv: Sequence[str]) -> None:
    if len(argv) > 1:
        raise app.UsageError("Too many command-line arguments.")

    input_file = pathlib.Path(_INPUT_FILE.value)
    transcript_file = pathlib.Path(_TRANSCRIPT_FILE.value)
    lock_file = transcript_file.with_suffix(".lock")

    try:
        with open(input_file, "r") as f:
            turn_input = yaml.safe_load(f)
    except FileNotFoundError:
        logging.exception("Input file not found: %s", input_file)
        sys.exit(1)
    except yaml.YAMLError:
        logging.exception("Input file is invalid YAML: %s", input_file)
        sys.exit(1)

    if not turn_input:
        logging.error("Input file is empty: %s", input_file)
        sys.exit(1)

    try:
        with file_lock(lock_file):

            transcript_data = {}
            if transcript_file.exists():
                try:
                    with open(transcript_file, "r") as f:
                        transcript_data = yaml.safe_load(f) or {}
                except yaml.YAMLError:
                    logging.exception(
                        "Transcript file is invalid YAML: %s", transcript_file
                    )
                    sys.exit(1)

            try:
                updated_data = process_append_turn(
                    transcript_data=transcript_data, turn_input=turn_input
                )
            except ValueError as e:
                logging.error("Validation error: %s", e)
                sys.exit(1)

            with open(transcript_file, "w") as f:
                yaml.dump(
                    updated_data,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                    width=1000,
                )
    except OSError:
        logging.exception("File operation failed during sync block")
        sys.exit(1)

    logging.info(
        "Successfully appended turn from %s to %s",
        input_file,
        transcript_file,
    )


if __name__ == "__main__":
    app.run(main)
