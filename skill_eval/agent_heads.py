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

"""Agent Head implementations for CXAS skill benchmarking."""

from __future__ import annotations

import asyncio
import glob
import os
import pathlib
import re
import shutil
import sys
import textwrap
import uuid
from collections.abc import Sequence
from typing import Final

import google.auth
from absl import logging
from google.antigravity import Agent, LocalAgentConfig
from google.antigravity.hooks import policy

from skill_eval import benchmark, scenario, trajectory_extractor


def _get_default_project_id() -> str | None:
    """Discovers the GCP project ID dynamically from ADC credentials."""
    try:
        _, project_id = google.auth.default()
        if project_id:
            return project_id
    except Exception:
        pass
    return None


class ScaffoldingTestAgent(benchmark.BaseAgentHead):
    """A mock agent head that returns pre-defined scaffolding responses."""

    def __init__(self, scenario_name: str, name: str = "ScaffoldingTestAgent"):
        self._name = name
        self._scenario_name = scenario_name
        self._session_id = uuid.uuid4().hex
        self._turn = 0

    async def send_message(self, message: str) -> str:
        self._turn += 1
        # Simulate a tool call on Turn 2
        if self._turn == 2:
            logging.info("[%s] Simulating background tool call...", self.name)

        return (
            f"Mock response from {self._name} to: {message[:50]}... (Turn"
            f" {self._turn})"
        )

    def get_tool_calls_count_last_turn(self) -> int:
        return 1 if self._turn == 2 else 0

    def get_tool_interactions_last_turn(
        self,
    ) -> Sequence[benchmark.ToolInteraction]:
        if self._turn == 2:
            return [
                benchmark.ToolInteraction(
                    name="mock_tool_call",
                    args={"param": "value"},
                    output='{"status": "ok"}',
                    thought="I should call a tool to verify this.",
                )
            ]
        return []

    def get_events_last_turn(self) -> Sequence[benchmark.TrajectoryEvent]:
        if self._turn == 2:
            return [
                benchmark.ToolCallEvent(
                    call_id="mock_call_1",
                    name="mock_tool_call",
                    args={"param": "value"},
                    source="main",
                ),
                benchmark.ToolResultEvent(
                    call_id="mock_call_1",
                    output='{"status": "ok"}',
                    is_error=False,
                    source="main",
                ),
            ]
        return []

    async def initialize(self) -> None:
        self._turn = 0

    @property
    def name(self) -> str:
        return self._name

    def get_log_files(self) -> Sequence[str]:
        del self  # Unused in this implementation.
        return []

    async def close(self) -> None:
        pass


class AntigravityAgentHead(benchmark.BaseAgentHead):
    """Agent head powered by the public Google Antigravity SDK."""

    _TIMEOUT_SECONDS: Final[int] = 1200

    def __init__(
        self,
        scenario_name: str,
        scenario_path: str,
        *,
        ls_address: str | None = None,
        ls_csrf_token: str | None = None,
        model: str = "gemini-3.5-flash",
        project: str | None = None,
        location: str = "us",
        assets: list[str] | None = None,
        setup_commands: Sequence[str] | None = None,
        run_uuid: str | None = None,
        keep_workspaces: bool = False,
    ):
        self._scenario_name = scenario_name
        self._session_id = uuid.uuid4().hex
        path_obj = pathlib.Path(scenario_path)
        clean_name = re.sub(r"[^a-zA-Z0-9_]+", "_", path_obj.stem).strip("_")
        self._base_prefix = (
            f"/tmp/cxas_skill_eval/antigravity_{clean_name}_{self._session_id}"
        )
        self._model = model
        self._project = project or _get_default_project_id()
        self._location = location
        self._assets = assets or []
        self._scenario_path = scenario_path
        self._setup_commands = tuple(setup_commands) if setup_commands else ()
        self._run_uuid = run_uuid
        self._agent: Agent | None = None
        self._tool_calls_last_turn = 0
        self._tool_interactions_last_turn: list[benchmark.ToolInteraction] = []
        self._events_last_turn: Sequence[benchmark.TrajectoryEvent] = []
        self._name = "Antigravity"
        self._trajectory_extractor = trajectory_extractor.TrajectoryExtractor()
        self._keep_workspaces = keep_workspaces
        self._original_env: dict[str, str | None] = {}

    @property
    def _workspace_dir(self) -> str:
        """Absolute path to the workspace directory where agent operates."""
        return f"{self._base_prefix}_ws"

    async def _run_subprocess_cmd(self, args: list[str], cwd: str) -> None:
        """Executes a subprocess command inside the given directory."""
        logging.info("[%s] Running command: %s", self.name, " ".join(args))
        env = os.environ.copy()
        process = await asyncio.create_subprocess_exec(
            args[0],
            *args[1:],
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            stdout_str = stdout.decode().strip()
            stderr_str = stderr.decode().strip()
            logging.error(
                "[%s] Command failed: %s\nSTDOUT:\n%s\nSTDERR:\n%s",
                self.name,
                " ".join(args),
                stdout_str,
                stderr_str,
            )
            raise RuntimeError(
                f"Command '{' '.join(args)}' failed with exit code"
                f" {process.returncode}"
            )

    async def initialize(self) -> None:
        """Initializes the conversation session and the agent."""
        await self.close()
        self._trajectory_extractor = trajectory_extractor.TrajectoryExtractor()

        # Resolve absolute paths to the root repository directory
        skill_eval_dir = pathlib.Path(__file__).parent.resolve()
        root_repo_dir = skill_eval_dir.parent.resolve()

        os.makedirs(self._workspace_dir, exist_ok=True)

        for asset in self._assets:
            src_path = scenario.get_asset_path(self._scenario_path, asset)
            try:
                dst_path = os.path.join(self._workspace_dir, asset)
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                if os.path.isdir(src_path):
                    if os.path.exists(dst_path):
                        shutil.rmtree(dst_path)
                    shutil.copytree(src_path, dst_path)
                else:
                    shutil.copy(src_path, dst_path)
                logging.info(
                    "[%s] Copied asset %s to %s", self.name, src_path, dst_path
                )
                if self._run_uuid:
                    self._replace_uuid_in_dir(
                        dst_path, self._run_uuid, self._project
                    )
            except Exception as e:
                logging.exception(
                    "[%s] Failed to copy asset %s", self.name, asset
                )
                raise RuntimeError(f"Failed to copy asset {asset}: {e}") from e

        # Guarantee full workspace writability to resolve read-only checkouts
        # permissions errors recursively.
        self._make_workspace_writable()

        # Execute isolated workspace environment setup

        await self._run_subprocess_cmd(
            ["uv", "venv", "--python", sys.executable, "--system-site-packages"], self._workspace_dir
        )

        local_venv_path = os.path.join(self._workspace_dir, ".venv")
        site_packages_glob = glob.glob(
            os.path.join(local_venv_path, "lib", "python*", "site-packages")
        )
        if not site_packages_glob:
            raise RuntimeError(
                "Could not find site-packages directory inside isolated venv:"
                f" {local_venv_path}"
            )
        local_site_packages = site_packages_glob[0]

        # 1. Zero-Build local package linkage (.pth file)
        src_dir = os.path.join(root_repo_dir, "src")
        pth_file = os.path.join(local_site_packages, "cxas-scrapi.pth")

        # Resolve active parent virtualenv site-packages directory dynamically
        parent_site_packages = None
        for p in sys.path:
            if "site-packages" in p:
                parent_site_packages = p
                break

        with open(pth_file, "w") as f:
            f.write(f"{src_dir}\n")
            if parent_site_packages:
                f.write(f"{parent_site_packages}\n")

        logging.info(
            "[%s] Wrote local .pth link file: %s -> %s (parent venv: %s)",
            self.name,
            pth_file,
            src_dir,
            parent_site_packages,
        )

        # 2. Programmatic cxas CLI wrap script
        local_venv_bin = os.path.join(local_venv_path, "bin")
        local_venv_cxas = os.path.join(local_venv_bin, "cxas")
        local_venv_python = os.path.join(local_venv_bin, "python3")

        cxas_script_content = textwrap.dedent(f"""\
        #!{local_venv_python}
        import sys
        sys.path.insert(0, {src_dir!r})
        from cxas_scrapi.cli.main import main
        if __name__ == '__main__':
            sys.exit(main())
        """)

        with open(local_venv_cxas, "w") as f:
            f.write(cxas_script_content)

        # Make CLI script executable
        os.chmod(local_venv_cxas, 0o755)
        logging.info(
            "[%s] Registered local cxas executable: %s",
            self.name,
            local_venv_cxas,
        )

        # Symlink or copy parent virtualenv's share/cxas-scrapi to child venv
        parent_skills_dir = os.path.join(sys.prefix, "share", "cxas-scrapi")
        local_share_dir = os.path.join(local_venv_path, "share", "cxas-scrapi")

        if os.path.exists(parent_skills_dir):
            os.makedirs(os.path.dirname(local_share_dir), exist_ok=True)
            try:
                os.symlink(parent_skills_dir, local_share_dir)
                logging.info(
                    "[%s] Symlinked parent shared skills: %s -> %s",
                    self.name,
                    local_share_dir,
                    parent_skills_dir,
                )
            except FileExistsError:
                pass
            except Exception as e:
                shutil.copytree(
                    parent_skills_dir, local_share_dir, dirs_exist_ok=True
                )
                logging.info(
                    "[%s] Copied parent shared skills: %s -> %s due to: %s",
                    self.name,
                    local_share_dir,
                    parent_skills_dir,
                    e,
                )

        instructions = (
            "You are a specialized virtual agent for designing and deploying "
            "virtual agents. You have the `cxas` CLI tool in your PATH and the "
            "GCP project is initialized."
        )

        # Prepare local agent configuration (Vertex-only evaluation)
        if not self._project:
            raise ValueError(
                "A GCP project ID (--project) is required for Vertex-only evaluation."
            )
        config = LocalAgentConfig(
            workspaces=[self._workspace_dir],
            policies=[policy.allow_all()],
            model=self._model,
            system_instructions=instructions,
            vertex=True,
            project=self._project,
            location=self._location,
        )

        # Capture original environment values for persistent injection
        self._original_env = {
            "PATH": os.environ.get("PATH"),
            "VIRTUAL_ENV": os.environ.get("VIRTUAL_ENV"),
            "GCLOUD_PROJECT": os.environ.get("GCLOUD_PROJECT"),
            "GOOGLE_CLOUD_PROJECT": os.environ.get("GOOGLE_CLOUD_PROJECT"),
            "CXAS_PROJECT_ID": os.environ.get("CXAS_PROJECT_ID"),
            "CXAS_LOCATION": os.environ.get("CXAS_LOCATION"),
        }

        local_venv_path = os.path.join(self._workspace_dir, ".venv")
        local_venv_bin = os.path.join(local_venv_path, "bin")

        os.environ["VIRTUAL_ENV"] = local_venv_path
        if self._original_env["PATH"]:
            os.environ["PATH"] = (
                f"{local_venv_bin}{os.pathsep}{self._original_env['PATH']}"
            )
        else:
            os.environ["PATH"] = local_venv_bin

        if self._project:
            os.environ["GCLOUD_PROJECT"] = self._project
            os.environ["GOOGLE_CLOUD_PROJECT"] = self._project
            os.environ["CXAS_PROJECT_ID"] = self._project
            os.environ["CXAS_LOCATION"] = self._location

        await self._run_setup_commands()

        self._agent = Agent(config)
        await self._agent.__aenter__()

    async def send_message(self, message: str) -> str:
        """Sends a message to the agent and returns its response string."""
        if not self._agent:
            raise RuntimeError(
                "Antigravity agent not initialized. Call initialize()."
            )

        self._tool_calls_last_turn = 0
        self._events_last_turn = []
        self._tool_interactions_last_turn = []

        # Chat with the agent
        chat_response = await self._agent.chat(message)
        response_text = await chat_response.text()

        # Walk step history to extract events
        self._events_last_turn = self._trajectory_extractor.extract_new_events(
            self._agent.conversation.history
        )
        self._tool_calls_last_turn = len(
            [
                e
                for e in self._events_last_turn
                if isinstance(e, benchmark.ToolCallEvent)
            ]
        )

        # Populate legacy tool interactions for backward compatibility
        for ev in self._events_last_turn:
            if isinstance(ev, benchmark.ToolCallEvent):
                output_val = ""
                for rev in self._events_last_turn:
                    if (
                        isinstance(rev, benchmark.ToolResultEvent)
                        and rev.call_id == ev.call_id
                    ):
                        output_val = rev.output
                        break
                self._tool_interactions_last_turn.append(
                    benchmark.ToolInteraction(
                        name=ev.name,
                        args=ev.args,
                        output=output_val or None,
                        thought=None,
                    )
                )

        logging.info("[%s] Response: %s", self.name, response_text)
        return response_text

    def get_tool_calls_count_last_turn(self) -> int:
        return self._tool_calls_last_turn

    def get_tool_interactions_last_turn(
        self,
    ) -> Sequence[benchmark.ToolInteraction]:
        return self._tool_interactions_last_turn

    def get_events_last_turn(self) -> Sequence[benchmark.TrajectoryEvent]:
        return self._events_last_turn

    def get_log_files(self) -> Sequence[str]:
        return []

    def get_trajectory_id(self) -> str | None:
        """Returns the trajectory ID associated with this head."""
        if self._agent and self._agent.conversation:
            return self._agent.conversation.conversation_id
        return None

    async def _run_setup_commands(self) -> None:
        if not self._setup_commands:
            return

        logging.info(
            "[%s] [%s] Running setup commands...", self.name, self._session_id
        )
        venv_path = os.path.join(self._workspace_dir, ".venv")
        env = os.environ.copy()
        if os.path.exists(venv_path):
            env["VIRTUAL_ENV"] = venv_path
            env["PATH"] = (
                f"{os.path.join(venv_path, 'bin')}:{env.get('PATH', '')}"
            )

        for cmd in self._setup_commands:
            logging.info(
                "[%s] [%s] Running setup command: %s",
                self.name,
                self._session_id,
                cmd,
            )
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=self._workspace_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=300.0
                )
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError(f"Setup command timed out: {cmd}") from None

            if proc.returncode != 0:
                raise RuntimeError(
                    f"Setup command failed with code {proc.returncode}: {cmd}\n"
                    f"Stdout: {stdout.decode()}\n"
                    f"Stderr: {stderr.decode()}"
                )

    async def close(self) -> None:
        """Closes the conversation and the agent session."""
        if self._agent:
            try:
                await self._agent.__aexit__(None, None, None)
            except Exception:
                pass
            self._agent = None
            self._events_last_turn = []

        # Restore original environment values
        if self._original_env:
            for k, v in self._original_env.items():
                if v is not None:
                    os.environ[k] = v
                elif k in os.environ:
                    del os.environ[k]
            self._original_env = {}

        # Cleanup dynamic apps created during run
        if self._run_uuid and self._project:
            venv_path = os.path.join(self._workspace_dir, ".venv")
            if os.path.exists(venv_path):
                env = os.environ.copy()
                env["VIRTUAL_ENV"] = venv_path
                env["PATH"] = (
                    f"{os.path.join(venv_path, 'bin')}:{env.get('PATH', '')}"
                )

                logging.info(
                    "[%s] [%s] Discovering active sandboxes for cleanup...",
                    self.name,
                    self._session_id,
                )
                try:
                    # 1. List all apps safely in the dynamic location using
                    # subprocess exec.
                    list_proc = await asyncio.create_subprocess_exec(
                        "cxas",
                        "apps",
                        "list",
                        "--project-id",
                        self._project,
                        "--location",
                        self._location,
                        cwd=self._workspace_dir,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env,
                    )
                    stdout, _ = await asyncio.wait_for(
                        list_proc.communicate(), timeout=60.0
                    )
                    if list_proc.returncode == 0:
                        lines = stdout.decode().split("\n")

                        for line in lines:
                            if self._run_uuid in line:
                                # Extract the unique fully qualified App
                                # Resource Name directly.
                                if "projects/" in line:
                                    idx = line.find("projects/")
                                    app_resource_name = (
                                        line[idx:].rstrip(")").strip()
                                    )
                                    logging.info(
                                        "[%s] [%s] Deleting dynamic sandbox "
                                        "app resource: %s",
                                        self.name,
                                        self._session_id,
                                        app_resource_name,
                                    )
                                    try:
                                        # Safe, individual deletion using
                                        # subprocess exec by resource name.
                                        del_proc = await asyncio.create_subprocess_exec(
                                            "cxas",
                                            "delete",
                                            "--app-name",
                                            app_resource_name,
                                            "--force",
                                            cwd=self._workspace_dir,
                                            stdout=asyncio.subprocess.PIPE,
                                            stderr=asyncio.subprocess.PIPE,
                                            env=env,
                                        )
                                        await asyncio.wait_for(
                                            del_proc.communicate(), timeout=60.0
                                        )
                                    except Exception:
                                        logging.exception(
                                            "[%s] [%s] Failed to delete app "
                                            "resource %s",
                                            self.name,
                                            self._session_id,
                                            app_resource_name,
                                        )
                except Exception as e:
                    logging.warning(
                        "[%s] [%s] Exception during dynamic sandbox "
                        "cleanup listing: %s",
                        self.name,
                        self._session_id,
                        e,
                    )

    def _replace_uuid_in_dir(
        self, path: str, run_uuid: str, project_id: str | None = None
    ) -> None:
        if os.path.isdir(path):
            for root, _, files in os.walk(path):
                for f in files:
                    file_path = os.path.join(root, f)
                    self._replace_uuid_in_file(file_path, run_uuid, project_id)
        else:
            self._replace_uuid_in_file(path, run_uuid, project_id)

    def _replace_uuid_in_file(
        self, file_path: str, run_uuid: str, project_id: str | None = None
    ) -> None:
        # Only replace in text files to prevent binary corruption
        if file_path.endswith(
            (".json", ".txt", ".yaml", ".yml", ".md", ".jsonl")
        ):
            try:
                with open(file_path, encoding="utf-8") as fp:
                    content = fp.read()
                modified = False
                if "{uuid}" in content:
                    content = content.replace("{uuid}", run_uuid)
                    modified = True
                if project_id and "{project_id}" in content:
                    content = content.replace("{project_id}", project_id)
                    modified = True
                if modified:
                    try:
                        os.chmod(file_path, 0o644)
                    except OSError:
                        pass
                    with open(file_path, "w", encoding="utf-8") as fp:
                        fp.write(content)
                    logging.info(
                        "[%s] Replaced parameters in asset file: %s",
                        self.name,
                        file_path,
                    )
            except (OSError, ValueError):
                logging.exception(
                    "[%s] Failed to replace parameters in file %s",
                    self.name,
                    file_path,
                )

    def _make_workspace_writable(self) -> None:
        """Recursively chmods the staging workspace to be fully writable."""
        try:
            os.chmod(self._workspace_dir, 0o755)
        except OSError:
            pass
        for root, dirs, files in os.walk(self._workspace_dir):
            try:
                if not os.path.islink(root):
                    os.chmod(root, 0o755)
            except OSError:
                pass
            for d in dirs:
                path = os.path.join(root, d)
                if not os.path.islink(path):
                    try:
                        os.chmod(path, 0o755)
                    except OSError:
                        pass
            for f in files:
                path = os.path.join(root, f)
                if not os.path.islink(path):
                    try:
                        os.chmod(path, 0o644)
                    except OSError:
                        pass

    @property
    def name(self) -> str:
        return self._name
