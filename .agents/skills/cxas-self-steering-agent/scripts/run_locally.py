#!/usr/bin/env python3
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

r"""Runs a CXAS app's Python tools and before-model callback on your machine.

Each step is `tool_name=JSON_ARGS` or `callback`. All steps share one session
state, which starts from the app.json defaults plus --state, so you can walk
through a flow and catch Python errors before a push. Tools get `context.state`
and can call each other with `tools.<name>({...})`. A `callback` step renders
the instruction for the current state and runs the root agent's before-model
callback with the classes from cxas_scrapi.utils.callback_libs, then shows
what it returned or which tools the model would see.

Example:
  python3 run_locally.py cxas_app/my-agent callback \
    'open_playbook={"playbook_name": "order_support", "rationale": "test"}' \
    'load_task={"task_name": "cancel_order", "rationale": "test"}' \
    'lookup_order={"order_number": "1001"}' \
    callback
"""

import argparse
import base64
import copy
import datetime
import json
import pathlib
import re
import types
from collections.abc import Callable
from typing import Any, Optional

import jinja2

from cxas_scrapi.utils import callback_libs

COMMENT = re.compile(r"\{\{#.*?#\}\}", re.S)


class ToolResponse:
    """What `tools.<name>(...)` returns; `.json()` gives `{"result": value}`."""

    def __init__(self, value: Any):
        """Initializes the response.

        Args:
          value: What the tool function returned.
        """
        self._value = value

    def json(self) -> dict[str, Any]:
        return {"result": self._value}


class _ToolsProxy:
    """Stands in for the `tools` global CES gives tool and callback code."""

    def __init__(self, runner: "Runner"):
        """Initializes the proxy.

        Args:
          runner: Loads and runs the tool functions.
        """
        self._runner = runner

    def __getattr__(self, name: str) -> Callable[..., ToolResponse]:
        def call(
            args: dict[str, Any] | None = None, **kwargs: Any
        ) -> ToolResponse:
            tool = self._runner.load_tool(name)
            return ToolResponse(tool(**(args or {}), **kwargs))

        return call


class Runner:
    """Runs an app's tools and callback against one shared session state."""

    def __init__(self, app_dir: pathlib.Path, state: dict[str, Any]):
        """Initializes the runner.

        Args:
          app_dir: The app directory, as `cxas pull` writes it.
          state: The session state, which every step updates in place.
        """
        self.app_dir = app_dir
        self.app = json.loads(
            (app_dir / "app.json").read_text(encoding="utf-8")
        )
        root = self.app["rootAgent"]
        self.agent_dir = app_dir / "agents" / root
        self.agent = json.loads(
            (self.agent_dir / f"{root}.json").read_text(encoding="utf-8")
        )
        self.state = state
        self.context = types.SimpleNamespace(
            state=state, session_id="local-test"
        )
        self.tools = _ToolsProxy(self)
        self._loaded = {}

    def load_tool(self, name: str) -> Callable[..., Any]:
        """Returns the function of the Python tool `name`, loading it once."""
        if name not in self._loaded:
            path = (
                self.app_dir
                / "tools"
                / name
                / "python_function"
                / "python_code.py"
            )
            if not path.exists():
                raise SystemExit(
                    f"no Python tool named {name!r} ({path} is missing)"
                )
            namespace = {
                "context": self.context,
                "tools": self.tools,
                "__name__": name,
            }
            # Tool code expects the globals CES provides, so exec it in place.
            code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
            exec(code, namespace)  # pylint: disable=exec-used
            self._loaded[name] = namespace[name]
        return self._loaded[name]

    def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        return self.load_tool(name)(**args)

    def run_callback(
        self,
    ) -> tuple[callback_libs.LlmResponse | None, callback_libs.LlmRequest]:
        """Runs the root agent's before-model callback on the current state.

        Returns:
          The callback's response (None when the model gets called) and the
          request it was given.

        Raises:
          SystemExit: The root agent has no before-model callback.
        """
        callbacks = self.agent.get("beforeModelCallbacks", [])
        if not callbacks:
            raise SystemExit("the root agent has no beforeModelCallbacks")
        code = (self.app_dir / callbacks[0]["pythonCode"]).read_text(
            encoding="utf-8"
        )
        namespace = {
            n: getattr(callback_libs, n)
            for n in dir(callback_libs)
            if not n.startswith("_")
        }
        namespace.update(
            Optional=Optional, Any=Any, json=json, tools=self.tools
        )
        compiled = compile(code, "before_model_callback", "exec")
        exec(compiled, namespace)  # pylint: disable=exec-used

        raw = (self.agent_dir / "instruction.txt").read_text(encoding="utf-8")
        source = COMMENT.sub(
            lambda m: "{% raw %}" + m.group(0) + "{% endraw %}", raw
        )
        rendered = jinja2.Environment().from_string(source).render(**self.state)
        cl = callback_libs
        request = cl.LlmRequest(
            contents=[
                cl.Content(
                    parts=[cl.Part(text="(customer message)")], role="user"
                )
            ],
            config=cl.GenerateContentConfig(
                systemInstruction=cl.Content(parts=[cl.Part(text=rendered)]),
                tools=[
                    cl.ToolDeclaration(
                        functionDeclarations=[
                            cl.FunctionDeclaration(name=t)
                            for t in self.agent.get("tools", [])
                        ]
                    )
                ],
            ),
        )
        callback_context = cl.CallbackContext(state=self.state)
        response = namespace["before_model_callback"](callback_context, request)
        self.state.clear()
        self.state.update(callback_context.state)
        return response, request


def _defaults(app: dict[str, Any]) -> dict[str, Any]:
    empty = {"STRING": "", "BOOLEAN": False, "NUMBER": 0, "INTEGER": 0}
    out = {}
    for var in app.get("variableDeclarations", []):
        schema = var.get("schema", {})
        out[var["name"]] = schema.get("default", empty.get(schema.get("type")))
    tz = app.get("timeZoneSettings", {}).get("timeZone", "UTC")
    out.setdefault("current_date", f"{datetime.date.today().isoformat()}[{tz}]")
    return out


def _changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    keys = set(before) | set(after)
    return {
        k: after.get(k, "<removed>")
        for k in sorted(keys)
        if before.get(k) != after.get(k)
    }


def _describe(response: callback_libs.LlmResponse) -> list[str]:
    """Returns one line for each part of the callback's response."""
    out = []
    for part in (response.content.parts if response.content else None) or []:
        if part.inline_data is not None:
            data = json.loads(base64.b64decode(part.inline_data.data))
            out.append(f"says: {data.get('content')!r}")
        elif part.function_call is not None:
            call = part.function_call
            out.append(f"calls {call.name}({json.dumps(call.args)})")
        elif part.text:
            out.append(f"text: {part.text!r}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.splitlines()[2:]),
    )
    parser.add_argument("app_dir")
    parser.add_argument(
        "steps",
        nargs="+",
        help="`tool_name=JSON_ARGS` or `callback`, in order.",
    )
    parser.add_argument(
        "--state",
        default="{}",
        help="Starting state overrides, as a JSON object.",
    )
    args = parser.parse_args()

    app_dir = pathlib.Path(args.app_dir)
    app = json.loads((app_dir / "app.json").read_text(encoding="utf-8"))
    state = {**_defaults(app), **json.loads(args.state)}
    runner = Runner(app_dir, state)

    for step in args.steps:
        before = copy.deepcopy(state)
        if step == "callback":
            print("> callback")
            response, request = runner.run_callback()
            if response is not None:
                for line in _describe(response):
                    print(f"  {line}")
            else:
                hidden = set(request.config.excluded_tools or [])
                visible = [
                    t for t in runner.agent.get("tools", []) if t not in hidden
                ]
                print(f"  calls the model with: {', '.join(visible)}")
        else:
            name, sep, raw_args = step.partition("=")
            try:
                tool_args = json.loads(raw_args) if sep else {}
            except json.JSONDecodeError as e:
                raise SystemExit(
                    f"arguments for {name} aren't valid JSON ({e}): {raw_args}"
                ) from e
            print(f"> {name}({json.dumps(tool_args)})")
            result = runner.call_tool(name, tool_args)
            print(f"  returns: {json.dumps(result, default=str)}")
        changes = _changes(before, state)
        if changes:
            print(f"  state: {json.dumps(changes, default=str)}")


if __name__ == "__main__":
    main()
