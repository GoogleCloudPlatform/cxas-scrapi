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

"""Well-typed Scenario abstraction for benchmark evaluation."""

import dataclasses
import pathlib
import re

import yaml


@dataclasses.dataclass(frozen=True)
class Rubric:
    """Definition of a criterion from the rubric."""

    criteria: str
    perfect: str
    good: str
    failed: str


def get_asset_path(scenario_path: str, asset_name: str) -> str:
    """Computes the path to an asset file for a given scenario."""
    p = pathlib.Path(scenario_path)
    return str(p.parent / p.stem / asset_name)


@dataclasses.dataclass(frozen=True)
class Setup:
    """Setup configuration for a scenario."""

    commands: tuple[str, ...] = dataclasses.field(default_factory=tuple)


@dataclasses.dataclass(frozen=True)
class Scenario:
    """A parsed benchmark scenario."""

    name: str
    text: str
    prompt: str
    rubric: list[Rubric]
    assets: list[str] = dataclasses.field(default_factory=list)
    setup: Setup = dataclasses.field(default_factory=Setup)

    @classmethod
    def from_yaml(cls, name: str, yaml_text: str) -> "Scenario":
        """Parses a Scenario from YAML text.

        Args:
          name: The name of the scenario.
          yaml_text: The YAML text of the scenario.

        Returns:
          A parsed Scenario object.
        """
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in scenario {name}: {e}") from e

        if not isinstance(data, dict):
            raise ValueError(f"Invalid YAML in scenario {name}: expected dict")

        required_fields = ["prompt", "rubric"]
        for field in required_fields:
            if field not in data:
                raise ValueError(f"Missing '{field}' in scenario {name}")

        if not isinstance(data["prompt"], str):
            raise ValueError(
                f"Invalid 'prompt' in scenario {name}: expected string"
            )

        rubric_data = data["rubric"]
        if not isinstance(rubric_data, list):
            raise ValueError(
                f"Invalid rubric in scenario {name}: expected list"
            )
        if not rubric_data:
            raise ValueError(f"Empty rubric in scenario {name}")

        rubric = []
        for r in rubric_data:
            if not isinstance(r, dict):
                raise ValueError(
                    f"Invalid rubric item in scenario {name}: expected dict"
                )

            if "criteria" not in r or not r["criteria"]:
                raise ValueError(
                    f"Missing or empty 'criteria' in rubric item in scenario {name}"
                )
            if "perfect" not in r or not r["perfect"]:
                raise ValueError(
                    f"Missing or empty 'perfect' in rubric item in scenario {name}"
                )

            criteria = r["criteria"]
            perfect = r["perfect"]
            good = r.get("good", "Met only after nudging or with errors")
            failed = r.get("failed", "Not met")

            for field_name, field_val in [
                ("criteria", criteria),
                ("perfect", perfect),
                ("good", good),
                ("failed", failed),
            ]:
                if not isinstance(field_val, str):
                    raise ValueError(
                        f"Invalid '{field_name}' in rubric item in scenario {name}:"
                        " expected string"
                    )

            rubric.append(
                Rubric(
                    criteria=criteria,
                    perfect=perfect,
                    good=good,
                    failed=failed,
                )
            )

        assets = data.get("assets")
        if assets is None:
            assets = []
        elif not isinstance(assets, list):
            raise ValueError(
                f"Invalid 'assets' in scenario {name}: expected list"
            )
        else:
            for i, asset in enumerate(assets):
                if not isinstance(asset, str):
                    raise ValueError(
                        f"Invalid asset at index {i} in scenario {name}: expected string"
                    )

        setup_data = data.get("setup")
        if setup_data is None:
            setup = Setup()
        elif not isinstance(setup_data, dict):
            raise ValueError(
                f"Invalid 'setup' in scenario {name}: expected dict"
            )
        else:
            setup_commands = setup_data.get("commands")
            if setup_commands is None:
                setup_commands = []
            elif not isinstance(setup_commands, list):
                raise ValueError(
                    f"Invalid 'setup.commands' in scenario {name}: expected list"
                )
            else:
                for i, cmd in enumerate(setup_commands):
                    if not isinstance(cmd, str):
                        raise ValueError(
                            f"Invalid setup command at index {i} in scenario {name}:"
                            " expected string"
                        )
            setup = Setup(commands=tuple(setup_commands))

        # Generate markdown text to maintain compatibility with reporting.py and
        # UserSimulator
        md_lines = []
        if "name" in data:
            md_lines.append(f"# {data['name']}")
            md_lines.append("")

        md_lines.append(data["prompt"])
        md_lines.append("")

        def append_formatted(prefix: str, text: str):
            text_lines = text.strip().split("\n")
            if not text_lines:
                return
            md_lines.append(f"{prefix}{text_lines[0]}")
            indent = " " * len(prefix)
            for line in text_lines[1:]:
                md_lines.append(f"{indent}{line}")

        md_lines.append("## Rubric")
        for r in rubric:
            md_lines.append(f"- **Criteria**: {r.criteria}")
            append_formatted("  - **Perfect**: ", r.perfect)
            append_formatted("  - **Good**: ", r.good)
            append_formatted("  - **Failed**: ", r.failed)

        md_text = "\n".join(md_lines)

        return cls(
            name=name,
            text=md_text,
            prompt=data["prompt"],
            rubric=rubric,
            assets=assets,
            setup=setup,
        )

    @classmethod
    def from_file(cls, path: str, *, name: str | None = None) -> "Scenario":
        """Loads and parses a Scenario from a file.

        Args:
          path: The path to the file.
          name: Optional name for the scenario. If not provided, the filename is
            used.

        Returns:
          A parsed Scenario object.

        Raises:
          ValueError: If the file cannot be read, has an unsupported extension, or
            the YAML content is malformed.
        """
        p = pathlib.Path(path)
        name = name if name is not None else path
        try:
            with open(path) as f:
                text = f.read()
        except OSError as err:
            raise ValueError(f"Could not read scenario file: {path!r}") from err

        if p.suffix in (".yaml", ".yml"):
            return cls.from_yaml(name, text)
        else:
            raise ValueError(
                f"Unsupported file extension: {p.suffix!r}. "
                "Only .yaml and .yml are supported."
            )


def get_safe_filename(name: str) -> str:
    """Sanitizes a name to be safe for use as a filename."""
    return re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_")
