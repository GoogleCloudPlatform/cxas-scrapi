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

import asyncio
import io
import json
import logging
import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict

import google.protobuf.duration_pb2
from google.cloud.ces_v1beta import types
from rich.console import Console

from cxas_scrapi.core.agents import Agents
from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.tools import Tools
from cxas_scrapi.core.versions import Versions
from cxas_scrapi.migration import (
    integrity_checks,
    ir_bundle,
    structural_consolidator,
    topology_wirer,
)
from cxas_scrapi.migration.ai_augment import AIAugment
from cxas_scrapi.migration.artifacts_builder import CXASAsyncArtifactBuilder
from cxas_scrapi.migration.code_block_migrator import CodeBlockMigrator
from cxas_scrapi.migration.cxas_topology_linker import CXASTopologyLinker
from cxas_scrapi.migration.data_models import (
    IRAgent,
    IRMetadata,
    IRTool,
    MigrationConfig,
    MigrationIR,
    MigrationStatus,
)
from cxas_scrapi.migration.designer import AsyncAgentDesigner
from cxas_scrapi.migration.dfcx_dep_analyzer import DependencyAnalyzer
from cxas_scrapi.migration.dfcx_exporter import ConversationalAgentsAPI
from cxas_scrapi.migration.dfcx_migration_reporter import DFCXMigrationReporter
from cxas_scrapi.migration.dfcx_parameter_extractor import (
    DFCXParameterExtractor,
)
from cxas_scrapi.migration.dfcx_playbook_converter import DFCXPlaybookConverter
from cxas_scrapi.migration.dfcx_tool_converter import DFCXToolConverter
from cxas_scrapi.migration.eval_generator import DeterministicEvalGenerator
from cxas_scrapi.migration.flow_visualizer import (
    FlowDependencyResolver,
    FlowTreeVisualizer,
)
from cxas_scrapi.migration.optimization_reporter import OptimizationReporter
from cxas_scrapi.migration.structural_consolidator import StructuralConsolidator
from cxas_scrapi.utils.gemini import GeminiGenerate
from cxas_scrapi.utils.secret_manager_utils import SecretManagerUtils

if TYPE_CHECKING:
    from cxas_scrapi.migration.ir_bundle import IRBundle

logger = logging.getLogger(__name__)


class MigrationService:
    """Orchestrates the end-to-end migration process from DFCX to CXAS."""

    def __init__(
        self,
        project_id: str,
        location: str = "global",
        gemini_location: str = "global",
        credentials=None,
        default_model: str = "gemini-3-flash-preview",
        ps_apps_client: Any = None,
        ps_agents_client: Any = None,
        ps_tools_client: Any = None,
        ps_toolsets_client: Any = None,
        secret_manager_client: Any = None,
        cx_api_client: Any = None,
    ):
        self.project_id = project_id
        self.location = location
        self.credentials = credentials
        self.default_model = default_model

        if ps_apps_client is None:
            self.ps_apps = Apps(
                project_id=self.project_id, location=self.location
            )
        else:
            self.ps_apps = ps_apps_client
        self.ps_agents = ps_agents_client
        self.ps_tools = ps_tools_client
        self.ps_toolsets = ps_toolsets_client
        if secret_manager_client is None:
            self.secret_manager = SecretManagerUtils(project_id=self.project_id)
        else:
            self.secret_manager = secret_manager_client
        self.cx_api = cx_api_client

        # Initialize internal clients
        self.gemini_client = GeminiGenerate(
            project_id=project_id,
            location=gemini_location,
            credentials=credentials,
            model_name="gemini-3.1-pro-preview",
            max_concurrent_requests=3,
        )

        self.exporter = ConversationalAgentsAPI()
        self.designer = AsyncAgentDesigner(gemini_client=self.gemini_client)
        self.artifacts_builder = CXASAsyncArtifactBuilder(
            gemini_client=self.gemini_client
        )
        self.eval_generator = None
        self.code_block_migrator = CodeBlockMigrator(
            ps_tools_client=self.ps_tools, ai_augment_client=None
        )
        self.ai_augment = AIAugment(gemini_client=self.gemini_client)
        self.reporter = DFCXMigrationReporter(gemini_client=self.gemini_client)
        self.tool_converter = DFCXToolConverter(
            secret_manager=self.secret_manager, reporter=self.reporter
        )
        self.playbook_converter = DFCXPlaybookConverter(reporter=self.reporter)
        self.topology_linker = CXASTopologyLinker(
            ps_agents_client=self.ps_agents,
            ps_apps_client=self.ps_apps,
            reporter=self.reporter,
        )

        self.ir = {}
        self.source_agent_data = None

    @classmethod
    def restore_from_bundle(
        cls,
        bundle,
        *,
        project_id: str | None = None,
        location: str | None = None,
    ) -> "MigrationService":
        """Recreate a `MigrationService` from a persisted :class:`IRBundle`.

        Used by stage1 / stage2 / stage3 to resume work against an already
        deployed app without going through a full :meth:`run_migration`
        cycle. Populates the runtime attributes that `run_migration` would
        normally set up after creating the app:

          * `ir` and `source_agent_data` from the bundle
          * `deployment_state` flagged as already deployed (so update-pass
            deploys don't try to create the app again)
          * `ps_agents` / `ps_tools` clients scoped to the existing app
          * `topology_linker.ps_agents` / `code_block_migrator.ps_tools`
            wired through to the new per-app clients
          * `eval_generator` ready for unit-test regeneration

        Args:
            bundle: The :class:`IRBundle` to restore from.
            project_id: Override the bundle's project ID. Defaults to
                `bundle.config.project_id`.
            location: Override the bundle's location. Defaults to whatever
                :meth:`IRBundle.resolve_location` returns (parsed from
                `bundle.app_url`, else "us").

        Returns:
            A `MigrationService` ready for update-pass deploys + Stage N
            optimizer calls.
        """
        pid = project_id or bundle.config.project_id
        loc = location or bundle.resolve_location()

        service = cls(
            project_id=pid,
            location=loc,
            default_model=bundle.config.model,
        )
        service.ir = bundle.ir
        service.source_agent_data = bundle.source_agent_data
        service.deployment_state = {
            "app_created": True,
            "vars_deployed": True,
            "app_timeout_configured": True,
            "app_model_configured": True,
        }
        service.eval_generator = DeterministicEvalGenerator(service.ir)

        app_resource = service.ir.metadata.app_resource_name
        if app_resource:
            service.ps_agents = Agents(app_name=app_resource)
            service.ps_tools = Tools(app_name=app_resource)
            if getattr(service, "topology_linker", None) is not None:
                service.topology_linker.ps_agents = service.ps_agents
            if getattr(service, "code_block_migrator", None) is not None:
                service.code_block_migrator.ps_tools = service.ps_tools

        return service

    # ------------------------------------------------------------------
    # Stage-level public methods
    #
    # These are the single source of truth for each post-migration stage.
    # `run_migration`'s `optimize_for_cxas` branch delegates to
    # `run_stage1` + `run_stage2`. Stage subcommands and skill scripts
    # call the same methods to avoid pipeline duplication.
    # ------------------------------------------------------------------

    async def run_stage1(
        self,
        *,
        consolidate: bool = False,
        bundle: "IRBundle | None" = None,
        gemini_client: GeminiGenerate | None = None,
        grouping_callback: Callable[..., Awaitable[dict | None]] | None = None,
        grouping_json_path: str | None = None,
        on_integrity_fail: str = "abort",
        version_label: str | None = "0.0.1",
        persist_bundle_path: str | None = None,
        console: Console | None = None,
    ) -> dict | None:
        """Run Stage 1: variable dedup + optional Gemini consolidation.

        Args:
            consolidate: Opt-in flag for Gemini-driven N→M structural
                consolidation. Default ``False`` preserves the historical
                ``run_migration`` behavior (variable dedup only).
            bundle: Required when ``consolidate=True`` — used to snapshot
                ``pre_consolidation_ir`` and persist the accepted grouping.
            gemini_client: Override the service's default Gemini client.
            grouping_callback: Async callable invoked after the consolidator
                proposes groupings, before integrity check + consolidate +
                deploy. Called with kwargs ``ir``, ``groupings``,
                ``consolidator``, ``root_key``, ``dep_summary`` so the
                callback can preview consolidation via
                ``consolidator.consolidate(...)`` and re-propose via
                ``consolidator.propose_groupings(...)``. Returns the
                accepted ``groupings`` dict (possibly edited) or ``None``
                to abort the consolidation step. The Stage 1 variable
                dedup still applies regardless. See
                :func:`cxas_scrapi.migration.grouping_review.interactive_review`
                for the canonical TUI implementation.
            grouping_json_path: If set, load groupings from this JSON file
                instead of calling Gemini.
            on_integrity_fail: How to handle ``check_consolidation_integrity``
                blocking errors. ``"abort"`` raises ``RuntimeError`` (default,
                safe). ``"warn"`` logs and continues. ``"ignore"`` is silent.
            version_label: CXAS Version ``display_name`` to create after
                the stage. ``None`` skips the checkpoint.
            persist_bundle_path: If set, save the updated bundle to this
                path after the stage.
            console: Rich console for progress output. Defaults to a fresh
                ``Console()`` (writes to stderr).

        Returns:
            The accepted grouping dict when ``consolidate=True`` and the
            user accepted; otherwise ``None``.
        """
        if consolidate and bundle is None:
            raise ValueError("run_stage1(consolidate=True) requires bundle=...")

        from cxas_scrapi.migration import stage_runner  # noqa: PLC0415

        console = console or Console()
        gemini = gemini_client or self.gemini_client

        # --- Variable dedup (always runs) -----------------------------------
        optimizer = await stage_runner.run_stage_with_redeploy(
            self, stage=1, console=console
        )
        stage_runner.merge_optimizer_logs_into_ir(self.ir, optimizer, "stage1")

        # --- Optional Gemini consolidation ----------------------------------
        accepted_groupings: dict | None = None
        if consolidate:
            accepted_groupings = await self._run_stage1_consolidation(
                bundle=bundle,
                gemini=gemini,
                grouping_callback=grouping_callback,
                grouping_json_path=grouping_json_path,
                on_integrity_fail=on_integrity_fail,
                console=console,
            )

        # --- CXAS Version checkpoint ----------------------------------------
        if version_label and self.ir.metadata.app_resource_name:
            description = "Stage 1: variable dedup" + (
                " + consolidation" if consolidate and accepted_groupings else ""
            )
            try:
                Versions(self.ir.metadata.app_resource_name).create_version(
                    display_name=version_label, description=description
                )
                logger.info(
                    "Created CXAS Version %s (%s).", version_label, description
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to create CXAS Version %s: %s",
                    version_label,
                    exc,
                )

        # --- Optional bundle persist ----------------------------------------
        if persist_bundle_path and bundle is not None:
            self.persist_bundle(
                bundle, persist_bundle_path, phase="stage1", status="ok"
            )

        return accepted_groupings

    async def _run_stage1_consolidation(
        self,
        *,
        bundle: "IRBundle",
        gemini: GeminiGenerate,
        grouping_callback: (
            Callable[[MigrationIR, dict], Awaitable[dict | None]] | None
        ),
        grouping_json_path: str | None,
        on_integrity_fail: str,
        console: Console,
    ) -> dict | None:
        """The Gemini consolidation block of Stage 1. Returns the accepted
        groupings, or ``None`` if the user aborted."""
        # 1. Build dep summary + detect root.
        analyzer = DependencyAnalyzer(self.source_agent_data)
        dep_summary = {
            "name_map": analyzer.name_map,
            "type_map": analyzer.type_map,
        }
        root_key = structural_consolidator.detect_root_key(
            self.ir, self.source_agent_data
        )

        # 2. Load or propose groupings.
        consolidator = StructuralConsolidator(
            self.ir, gemini, source_data=self.source_agent_data
        )
        if grouping_json_path:
            groupings = structural_consolidator.load_grouping(
                grouping_json_path
            )
            console.print(
                f"[cyan]Loaded groupings from {grouping_json_path}[/]"
            )
        else:
            console.print("[cyan]Asking Gemini to propose agent groupings…[/]")
            groupings = await consolidator.propose_groupings(
                root_key=root_key, dep_summary=dep_summary
            )

        # 3. Optional interactive review. The callback receives everything
        # it needs to preview consolidation + re-propose, all via kwargs so
        # it can pick out whichever args it actually uses.
        if grouping_callback is not None:
            reviewed = await grouping_callback(
                ir=self.ir,
                groupings=groupings,
                consolidator=consolidator,
                root_key=root_key,
                dep_summary=dep_summary,
            )
            if reviewed is None:
                logger.warning(
                    "Grouping review aborted — Stage 1 dedup applied, "
                    "consolidation skipped."
                )
                return None
            groupings = reviewed

        # 4. Validate.
        structural_consolidator.validate_groupings(self.ir, groupings, root_key)

        # 5. Snapshot pre-consolidation IR (for integrity check + rollback).
        bundle.pre_consolidation_ir = self.ir.model_copy(deep=True)

        # 6. Consolidate IR + persist grouping.
        pre_consolidation_ir = bundle.pre_consolidation_ir
        self.ir = consolidator.consolidate(groupings)
        bundle.grouping = groupings
        try:
            grouping_path = f"{bundle.config.target_name}_grouping.json"
            structural_consolidator.persist_grouping(groupings, grouping_path)
            logger.info("Persisted grouping → %s", grouping_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist grouping JSON: %s", exc)

        # 7. Synthesize per-group PIF instructions.
        status = await consolidator.synthesize_instructions(
            self.ir, groupings, per_group_timeout_s=600
        )
        ok_count = sum(1 for v in status.values() if v == "ok")
        timeout_count = sum(1 for v in status.values() if v == "timeout")
        error_count = len(status) - ok_count - timeout_count
        logger.info(
            "Synthesis complete: %d ok, %d timeout, %d error",
            ok_count,
            timeout_count,
            error_count,
        )

        # 7b. Heal trivial tool-ref drift before integrity check.
        # The 2B prompt now receives the full tool registry, but Gemini
        # still occasionally over-applies the ``_wrapper`` / ``_tool``
        # suffix pattern. This pass strips a suffix when the base ID
        # exists and the suffixed form doesn't — strictly safe
        # rewrites. Genuine hallucinations (refs with no near match)
        # are left for integrity_checks to surface.
        rewrites, unhealed = structural_consolidator.heal_tool_refs(self.ir)
        if rewrites:
            logger.info(
                "Healed %d tool-ref mismatches (e.g. %s)",
                len(rewrites),
                ", ".join(f"{k}→{v}" for k, v in list(rewrites.items())[:3]),
            )

        # 8. Pre-deploy integrity check.
        blocking, warnings = integrity_checks.check_consolidation_integrity(
            self.ir, pre_consolidation_ir
        )
        if warnings:
            for w in warnings:
                logger.warning("integrity warning: %s", w)
        if blocking:
            for b in blocking:
                logger.error("integrity blocking: %s", b)
            if on_integrity_fail == "abort":
                raise RuntimeError(
                    f"Integrity check found {len(blocking)} blocking "
                    f"error(s). Set on_integrity_fail='warn' or 'ignore' "
                    f"to proceed anyway. First: {blocking[0]}"
                )
            elif on_integrity_fail == "warn":
                logger.warning(
                    "%d blocking integrity errors — continuing under "
                    "on_integrity_fail='warn'.",
                    len(blocking),
                )
            # "ignore" → silent continuation

        # 9. Deploy consolidated agents.
        console.print("\n[cyan]Pushing consolidated agents to CXAS…[/]")
        await self._deploy_base_resources(is_update_pass=True)
        await self._deploy_pending_agents(is_update_pass=True)

        # 10. Topology link + set root + orphan cleanup.
        try:
            self.topology_linker.link_and_finalize_topology(
                self.ir, self.source_agent_data
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Topology linking failed: %s", exc)

        ok, msg = topology_wirer.set_app_root_agent(bundle)
        if ok:
            logger.info(msg)
        elif msg:
            logger.warning(msg)

        keep_resources = {
            a.resource_name for a in self.ir.agents.values() if a.resource_name
        }
        if self.ir.metadata.app_resource_name and keep_resources:
            deleted, remaining = topology_wirer.delete_orphan_agents(
                self.ir.metadata.app_resource_name,
                keep_resources=keep_resources,
            )
            if deleted or remaining:
                logger.info(
                    "Orphan cleanup: %d deleted, %d remaining.",
                    deleted,
                    remaining,
                )

        return groupings

    async def run_stage2(
        self,
        *,
        version_label: str | None = "0.0.2",
        generate_unit_tests: bool = False,
        unit_tests_path: str | None = None,
        run_lint: bool = False,
        write_report_to: str | None = None,
        bundle: "IRBundle | None" = None,
        persist_bundle_path: str | None = None,
        console: Console | None = None,
    ) -> None:
        """Run Stage 2: instruction state machines + tool mocks.

        Optionally regenerates deterministic unit tests, runs
        ``cxas pull`` + ``cxas lint``, and writes an
        :class:`OptimizationReporter` audit markdown.
        """
        from cxas_scrapi.migration import (  # noqa: PLC0415
            post_deploy_lint,
            stage_runner,
        )

        console = console or Console()

        # --- Optimize Stage 2 + redeploy ------------------------------------
        optimizer = await stage_runner.run_stage_with_redeploy(
            self, stage=2, console=console
        )
        stage_runner.merge_optimizer_logs_into_ir(self.ir, optimizer, "stage2")

        # --- CXAS Version checkpoint ----------------------------------------
        if version_label and self.ir.metadata.app_resource_name:
            try:
                Versions(self.ir.metadata.app_resource_name).create_version(
                    display_name=version_label,
                    description=(
                        "Stage 2: instruction state machines + tool mocks"
                    ),
                )
                logger.info("Created CXAS Version %s.", version_label)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to create CXAS Version %s: %s",
                    version_label,
                    exc,
                )

        # --- Optional unit-test regeneration --------------------------------
        test_counts: dict[str, int] = {}
        if generate_unit_tests:
            try:
                gen = DeterministicEvalGenerator(self.ir)
                by_agent: dict[str, list] = {}
                for agent_name in self.ir.agents:
                    cases = gen.generate_tests_for_agent(agent_name)
                    if cases:
                        by_agent[agent_name] = [
                            tc.model_dump(mode="json") for tc in cases
                        ]
                test_counts = {n: len(v) for n, v in by_agent.items()}
                if unit_tests_path:
                    with open(unit_tests_path, "w") as f:
                        json.dump(by_agent, f, indent=2, default=str)
                    logger.info(
                        "Regenerated %d tests for %d agents → %s",
                        sum(test_counts.values()),
                        len(test_counts),
                        unit_tests_path,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Unit test regeneration failed: %s", exc)

        # --- Optional post-deploy lint --------------------------------------
        lint_passed: bool | None = None
        lint_output = ""
        if run_lint:
            try:
                (
                    lint_passed,
                    lint_output,
                ) = await post_deploy_lint.run_post_deploy_lint(self, console)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Lint did not run: %s", exc)

        # --- Optional OptimizationReporter audit markdown -------------------
        if write_report_to:
            try:
                stage1_logs = self.ir.optimization_logs.get("stages", {}).get(
                    "stage1"
                )
                stage2_logs = self.ir.optimization_logs.get("stages", {}).get(
                    "stage2"
                )
                reporter = OptimizationReporter()
                target_name = (
                    bundle.config.target_name if bundle else "(unknown)"
                )
                reporter.set_app_info(
                    "(see bundle)",
                    target_name,
                    self.ir.metadata.app_resource_name or "",
                    bundle.app_url if bundle else "",
                )
                if bundle and bundle.grouping:
                    before_count = len(
                        bundle.source_agent_data.playbooks
                    ) + len(bundle.source_agent_data.flows)
                    reporter.set_grouping(
                        bundle.grouping,
                        before_count=before_count,
                        after_count=len(self.ir.agents),
                        path=f"{target_name}_grouping.json",
                    )
                reporter.set_optimizer_logs(stage1_logs, stage2_logs)
                if bundle and bundle.version_checkpoints:
                    reporter.set_version_checkpoints(bundle.version_checkpoints)
                if test_counts:
                    reporter.set_unit_test_summary(
                        test_counts, unit_tests_path or ""
                    )
                if lint_passed is not None:
                    reporter.set_lint_result(lint_passed, lint_output)
                reporter.export(write_report_to)
                logger.info("Optimization report → %s", write_report_to)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Report generation failed: %s", exc)

        # --- Optional bundle persist ----------------------------------------
        if persist_bundle_path and bundle is not None:
            self.persist_bundle(
                bundle, persist_bundle_path, phase="stage2", status="ok"
            )

    async def run_stage3(
        self,
        *,
        bundle: "IRBundle",
        mode: str = "hub",
        set_root: bool = True,
        dry_run: bool = False,
        persist_bundle_path: str | None = None,
    ) -> tuple[int, int, int]:
        """Stage 3: parent-child topology wiring for consolidated agents.

        Requires a consolidated bundle (``bundle.grouping`` must be set).
        Returns ``(updated, skipped, failed)`` counts from
        :func:`topology_wirer.apply_topology`.
        """
        if not bundle.grouping:
            raise RuntimeError(
                "Stage 3 requires consolidated bundle.grouping; run "
                "stage1 with consolidate=True first."
            )

        children = topology_wirer.compute_group_children(bundle, mode=mode)
        updated, skipped, failed = topology_wirer.apply_topology(
            bundle, children, dry_run=dry_run
        )
        logger.info(
            "Stage 3 wiring: updated=%d skipped=%d failed=%d",
            updated,
            skipped,
            failed,
        )

        if set_root and not dry_run:
            ok, msg = topology_wirer.set_app_root_agent(bundle)
            if ok:
                logger.info(msg)
            elif msg:
                logger.warning(msg)

        if persist_bundle_path and not dry_run:
            self.persist_bundle(
                bundle,
                persist_bundle_path,
                phase="stage3",
                status="ok" if failed == 0 else "partial",
                notes=(f"updated={updated} skipped={skipped} failed={failed}"),
            )

        return updated, skipped, failed

    def persist_bundle(
        self,
        bundle: "IRBundle",
        path: str,
        *,
        phase: str | None = None,
        status: str = "ok",
        notes: str = "",
    ) -> str:
        """Snapshot ``self.ir`` into the bundle and write it to ``path``.

        Optionally appends a stage_history entry when ``phase`` is set.
        Synchronous — pure local I/O. Returns ``path``.
        """
        bundle.ir = self.ir
        if phase:
            ir_bundle.append_stage(
                bundle,
                phase,
                status,
                started_at=datetime.now(),
                notes=notes,
            )
        ir_bundle.save(bundle, path)
        return path

    def _inject_system_variables(self, dynamic_params: list = None):
        """Injects global system variables required by migration tooling and
        callbacks.
        """
        system_vars = [
            {
                "name": "mock_mode",
                "description": (
                    "Global toggle. If true, Python tool wrappers will "
                    "return mock data instead of executing "
                    "real backend API calls."
                ),
                "schema": {"type": "BOOLEAN", "default": False},
            },
            {
                "name": "first_turn",
                "description": (
                    "Tracks whether this is first turn of conversation. "
                    "Used by callbacks for deterministic greetings."
                ),
                "schema": {"type": "BOOLEAN", "default": True},
            },
            {
                "name": "no_input_retry_count",
                "description": (
                    "Tracks the number of consecutive times the user has "
                    "provided no input (silence). Used by callbacks."
                ),
                "schema": {"type": "INTEGER", "default": 0},
            },
        ]

        if dynamic_params:
            for param in dynamic_params:
                system_vars.append(
                    {
                        "name": param,
                        "description": f"Dynamic routing parameter: {param}",
                        "schema": {"type": "STRING"},
                    }
                )

        for sys_var in system_vars:
            name = sys_var["name"]
            if name not in self.ir.parameters:
                logger.info(f"  -> Injecting global '{name}' variable.")
                self.ir.parameters[name] = sys_var

    async def run_migration(
        self,
        source_cx_agent_id: str,
        config: MigrationConfig,
    ) -> None:
        """The comprehensive async executor for Hybrid Migration."""

        # --- 0. Data Loading & Preprocessing ---
        self.source_agent_data = (
            config.source_agent_data_override
            or self.exporter.fetch_full_agent_details(
                source_cx_agent_id, use_export=True
            )
        )
        if not self.source_agent_data:
            logger.error(
                "Migration failed: Could not retrieve source agent data."
            )
            return

        logger.info("\nPre-processing text fields (Playbook -> agent)...")
        self._preprocess_text_fields(self.source_agent_data)
        self.reporter.log_action(
            "Pre-processing",
            "Executed global text replacement: 'playbook' -> 'agent'",
        )

        logger.info(f"Starting Hybrid Migration for: {config.target_name}")

        # --- 1. Populate IR Metadata & Predictable IDs ---
        target_app_uuid = str(uuid.uuid4())
        target_app_resource_name = (
            f"projects/{self.project_id}/locations/{self.location}/apps/"
            f"{target_app_uuid}"
        )

        self.ir = MigrationIR(
            metadata=IRMetadata(
                app_name=config.target_name,
                app_id=target_app_uuid,
                app_resource_name=target_app_resource_name,
                default_model=config.model or self.default_model,
            )
        )
        self.deployment_state = {
            "app_created": False,
            "vars_deployed": False,
        }

        self.eval_generator = DeterministicEvalGenerator(self.ir)
        self.reporter.set_app_info(
            source_cx_agent_id, config.target_name, target_app_resource_name
        )

        # --- 2. Async Playbook Description Generation ---
        logger.info("Generating Playbook descriptions concurrently...")
        playbooks = self.source_agent_data.playbooks
        playbook_descriptions = {}
        if playbooks:
            tasks = [
                self.ai_augment.generate_agent_description(pb)
                for pb in playbooks
            ]
            desc_results = await asyncio.gather(*tasks, return_exceptions=True)

            for pb, desc in zip(playbooks, desc_results, strict=True):
                pb_name = pb["displayName"]
                if isinstance(desc, Exception):
                    logger.warning(
                        f"Failed to generate description for {pb_name}: {desc}"
                    )
                    playbook_descriptions[pb_name] = ""
                else:
                    playbook_descriptions[pb_name] = desc if desc else ""
                    if desc:
                        logger.info(
                            f"***Generated agent description***: {desc}"
                        )

        # --- 3. Populate IR Variables ---
        final_declarations, parameter_name_map = (
            DFCXParameterExtractor.migrate_parameters(
                self.source_agent_data, self.reporter
            )
        )
        for param in final_declarations:
            if len(self.ir.parameters) >= 90:
                logger.warning(
                    f"    - Variable limit reached! Stashing parameter "
                    f"'{param['name']}' for later LLM optimization pass."
                )
                unregistered = self.ir.optimization_logs.setdefault(
                    "unregistered_parameters", []
                )
                if param["name"] not in unregistered:
                    unregistered.append(param["name"])
                continue

            self.ir.parameters[param["name"]] = param

        self._inject_system_variables()

        # --- 4. Populate Standard Tools & Webhooks into IR ---
        logger.info("Processing Standard Tools and Webhooks...")
        cx_source_to_ir_tool_map = {}
        cx_tool_display_name_to_id_map = {}
        created_tool_ids = set()
        created_toolset_ids = set()

        def process_resource(cx_resource, is_webhook=False):
            if is_webhook:
                res = self.tool_converter.convert_webhook_to_openapi_toolset(
                    cx_resource
                )
            else:
                res = self.tool_converter.convert_cx_tool_to_ps_resource(
                    cx_resource
                )
            if not res:
                return

            display_name = cx_resource.get("displayName")
            if display_name:
                cx_tool_display_name_to_id_map[display_name] = cx_resource.get(
                    "name"
                )

            base_id = res["id"]
            final_id = base_id
            existing_ids = (
                created_toolset_ids
                if res["type"] == "TOOLSET"
                else created_tool_ids
            )

            suffix_counter = 2
            while final_id in existing_ids:
                suffix = f"_{suffix_counter}"
                final_id = f"{base_id[: 36 - len(suffix)]}{suffix}"
                suffix_counter += 1

            existing_ids.add(final_id)
            res["id"] = final_id

            if "name" in res["payload"]:
                res["payload"]["name"] = final_id
            if (
                "data_store_tool" in res["payload"]
                and "name" in res["payload"]["data_store_tool"]
            ):
                res["payload"]["data_store_tool"]["name"] = final_id

            collection = "toolsets" if res["type"] == "TOOLSET" else "tools"
            ir_tool = IRTool(
                id=final_id,
                name=f"{target_app_resource_name}/{collection}/{final_id}",
                type=res["type"],
                payload=res["payload"],
                operation_ids=res.get("operation_ids", []),
                status=MigrationStatus.COMPILED,
            )
            self.ir.tools[final_id] = ir_tool

            if cx_resource.get("name"):
                cx_source_to_ir_tool_map[cx_resource["name"]] = ir_tool

        for cx_tool in getattr(self.source_agent_data, "tools", []):
            process_resource(cx_tool, is_webhook=False)

        for cx_webhook in getattr(self.source_agent_data, "webhooks", []):
            w_val = (
                cx_webhook.get("value", cx_webhook)
                if isinstance(cx_webhook, dict)
                else cx_webhook
            )
            process_resource(w_val, is_webhook=True)

        # --- 5. Extract Code Blocks ---
        logger.info("Extracting and rewriting Python Code Blocks into IR...")
        code_blocks = self.source_agent_data.code_blocks
        master_inline_action_map = {}
        existing_tool_ids = set(self.ir.tools.keys())
        migrated_function_names = set()
        function_name_to_tool_map = {}
        tool_display_name_map = {
            tool.payload.get("displayName")
            or tool.payload.get("display_name", tool.id): tool.id
            for tool in self.ir.tools.values()
            if tool.payload
        }

        playbook_to_code_tools_map = {}
        playbook_to_code_dependencies_map = {}

        for playbook in playbooks:
            playbook_name = playbook["displayName"]
            playbook_to_code_tools_map[playbook_name] = []
            playbook_to_code_dependencies_map[playbook_name] = set()

            code = ""
            if "codeBlock" in playbook:
                cb_val = playbook["codeBlock"]
                if isinstance(cb_val, dict) and "code" in cb_val:
                    code = cb_val["code"]
                elif isinstance(cb_val, str):
                    for code_block in code_blocks:
                        if code_block.get("name") == cb_val:
                            code = code_block.get("code", "")
                            break

            if code:
                (
                    extracted_tools,
                    action_to_tool_map,
                    referenced_toolsets,
                    discovered_parameters,
                    routing_parameters,
                ) = self.code_block_migrator.extract_functions_to_ir(
                    code,
                    existing_tool_ids,
                    migrated_function_names,
                    function_name_to_tool_map,
                    self.ir.tools,
                    tool_display_name_map,
                    target_app_resource_name,
                    set(self.ir.parameters.keys()),
                )

                # Register newly discovered parameters subject to 95-limit
                for param_name in discovered_parameters:
                    if param_name not in self.ir.parameters:
                        if len(self.ir.parameters) >= 95:
                            logger.warning(
                                f"    - Skipping registration for discovered "
                                f"parameter '{param_name}' due to CXAS "
                                f"variable limit. Saving for later pass."
                            )
                            unregistered = self.ir.optimization_logs.setdefault(
                                "unregistered_parameters", []
                            )
                            if param_name not in unregistered:
                                unregistered.append(param_name)
                            continue
                        logger.info(
                            f"    - Registering newly discovered parameter "
                            f"from code block: {param_name}"
                        )
                        self.ir.parameters[param_name] = {
                            "name": param_name,
                            "description": (
                                "Auto-discovered parameter from code block."
                            ),
                            "schema": {
                                "type": "STRING"
                            },  # Default to STRING for unknown types
                        }

                # Explicitly inject routing parameters to bypass limits
                if routing_parameters:
                    self._inject_system_variables(list(routing_parameters))

                for tool in extracted_tools:
                    self.ir.tools[tool["id"]] = IRTool(
                        id=tool["id"],
                        name=tool["name"],
                        type=tool["type"],
                        payload=tool["payload"],
                        status=MigrationStatus.COMPILED,
                    )

                # Link ALL referenced Python tools (new and reused)
                for _func_name, tool_id in action_to_tool_map.items():
                    full_tool_name = (
                        f"{target_app_resource_name}/tools/{tool_id}"
                    )
                    if (
                        full_tool_name
                        not in playbook_to_code_tools_map[playbook_name]
                    ):
                        playbook_to_code_tools_map[playbook_name].append(
                            full_tool_name
                        )

                master_inline_action_map.update(action_to_tool_map)
                playbook_to_code_dependencies_map[playbook_name].update(
                    referenced_toolsets
                )

        # --- 6. Compile Playbooks into IR ---
        logger.info("Compiling Playbooks into IR payload...")
        cx_tool_display_name_to_id_map = {
            t["displayName"]: t_id
            for t_id, t in self.ir.tools.items()
            if "displayName" in t
        }

        for pb in playbooks:
            pb_name = pb["displayName"]
            generated_desc = playbook_descriptions.get(pb_name)
            agent_payload = (
                self.playbook_converter.convert_cx_playbook_to_ps_agent(
                    pb,
                    cx_source_to_ir_tool_map,
                    generated_desc,
                    parameter_name_map,
                    cx_tool_display_name_to_id_map,
                    master_inline_action_map,
                    self.default_model,
                )
            )

            # 1. Attach Code Block Python Tools
            code_tools = playbook_to_code_tools_map.get(playbook_name, [])
            for code_tool in code_tools:
                if code_tool not in agent_payload["tools"]:
                    agent_payload["tools"].append(code_tool)

            # 2. Attach Code Block Toolset Dependencies
            code_dependencies = playbook_to_code_dependencies_map.get(
                playbook_name, set()
            )
            for dep_toolset_name in code_dependencies:
                if not any(
                    toolset_entry.get("toolset") == dep_toolset_name
                    for toolset_entry in agent_payload["toolsets"]
                ):
                    agent_payload["toolsets"].append(
                        {"toolset": dep_toolset_name}
                    )

            self.ir.agents[pb_name] = IRAgent(
                type="PLAYBOOK",
                display_name=pb_name,
                description=generated_desc
                or pb.get("goal", "No description provided."),
                instruction=agent_payload["instruction"],
                tools=agent_payload["tools"],
                toolsets=agent_payload["toolsets"],
                model_settings=agent_payload["modelSettings"],
                raw_data=pb,
                status=MigrationStatus.COMPILED,
            )

        # --- 6. FAST DEPLOY (Phase 1) ---
        logger.info("FAST DEPLOY: Pushing Base Resources to CXAS...")
        await self._deploy_base_resources()
        await self._deploy_pending_agents()

        # --- 8. Background Processing for Flows (Phase 2) ---
        flows = self.source_agent_data.flows
        if flows:
            app_id = self.ir.metadata.app_id
            app_url = (
                f"https://ces.cloud.google.com/projects/{self.project_id}"
                f"/locations/{self.location}/apps/{app_id}"
            )
            logger.info(
                f"\nACCESS YOUR CXAS AGENT HERE:\n{app_url}\n\n"
                f"*(Note: Background processes are still running and more "
                f"sub-agents and other resources are currently being "
                f"migrated!)*\n"
            )
            logger.info(
                f"\nLaunching parallel Analysis & Architecture for "
                f"{len(flows)} flows..."
            )

            tasks = [
                self._process_single_flow(
                    flow, target_app_resource_name, parameter_name_map
                )
                for flow in flows
            ]
            await asyncio.gather(*tasks)

        # --- 10. Finalization & Topology Linking (Phase 4) ---
        logger.info("DEPLOYMENT & TOPOLOGY LINKING")
        self.topology_linker.link_and_finalize_topology(
            self.ir, self.source_agent_data
        )

        logger.info("MIGRATION STAGE COMPLETE, STARTING OPTIMIZATION")
        app_url = f"https://ces.cloud.google.com/projects/{self.project_id}/locations/{self.location}/apps/{self.ir.metadata.app_id}"
        logger.info(f"ACCESS YOUR CXAS AGENT HERE:\n{app_url}")

        # --- 11. OPTIMIZATION MODULE (Track 3) ---
        if config.optimize_for_cxas:
            # Pre-optimization snapshot — kept inline because it represents
            # "state before any stage", not a stage output. The numbered
            # stage Versions are created inside run_stage1 / run_stage2.
            logger.info("\n--- Creating Pre-Optimization Backup Version ---")
            try:
                Versions(target_app_resource_name).create_version(
                    display_name="0.0.1",
                    description="Initial agent version",
                )
                logger.info(
                    "Successfully created pre-optimization version backup "
                    "in CXAS."
                )
            except Exception as e:
                logger.warning(
                    f"Failed to create pre-optimization version backup: {e}"
                )

            logger.info("\n--- Executing Hybrid Optimization Module ---")
            # Stage 1: variable dedup only (no Gemini consolidation —
            # consolidate=False default preserves the historical behavior
            # of this branch). Version label "0.0.2" preserved for
            # back-compat with the existing 0.0.1 / 0.0.2 / 0.0.3 scheme.
            await self.run_stage1(version_label="0.0.2")
            # Stage 2: instruction state machines + tool mocks.
            # Version label "0.0.3" preserved for back-compat.
            await self.run_stage2(version_label="0.0.3")

        logger.info("\n" + "=" * 50)
        logger.info("MIGRATION COMPLETE!")
        app_url = (
            f"https://ces.cloud.google.com/projects/{self.project_id}"
            f"/locations/{self.location}/apps/{self.ir.metadata.app_id}"
        )
        logger.info(f"ACCESS YOUR CXAS AGENT HERE:\n{app_url}")
        logger.info("=" * 50 + "\n")

        self.reporter.export_and_download(
            f"{config.target_name}_migration_report.md"
        )

    async def _deploy_base_resources(self, is_update_pass: bool = False):
        """Deploys App, Variables, and Tools from the IR."""
        app_id_uuid = self.ir.metadata.app_id
        app_name = self.ir.metadata.app_name
        full_app_name = self.ir.metadata.app_resource_name

        # 1. Create App (If not already created)
        if not self.deployment_state.get("app_created"):
            logger.info(f"\nCreating CXAS App: '{app_name}'...")
            default_model = self.ir.metadata.default_model or "gemini-2.5-flash"
            ps_app = self.ps_apps.create_app(
                app_id=app_id_uuid,
                display_name=app_name,
                model_settings=types.ModelSettings(model=default_model),
            )
            if not ps_app:
                logger.error("❌ Failed to create App. Aborting deployment.")
                return
            self.deployment_state["app_created"] = True
            logger.info(f"   -> App Created: {full_app_name}")
            if self.ps_agents is None:
                self.ps_agents = Agents(app_name=full_app_name)
                self.topology_linker.ps_agents = self.ps_agents
            if self.ps_tools is None:
                self.ps_tools = Tools(app_name=full_app_name)
                self.code_block_migrator.ps_tools = self.ps_tools

        # 2. Deploy Consolidated App Configurations (Timeout, Variables)
        app_updates = {}

        # A. Speech Silence / Inactivity Timeout (WAI for CXAS/Polysynth)
        if (
            not self.deployment_state.get("app_timeout_configured")
            or is_update_pass
        ):
            no_speech_timeout = "7s"
            if (
                hasattr(self.source_agent_data, "no_speech_timeout")
                and self.source_agent_data.no_speech_timeout
            ):
                no_speech_timeout = self.source_agent_data.no_speech_timeout

            seconds = 7
            try:
                seconds = int(no_speech_timeout.replace("s", ""))
            except Exception:
                pass

            inactivity_duration = google.protobuf.duration_pb2.Duration(
                seconds=seconds
            )
            app_updates["audio_processing_config"] = (
                types.AudioProcessingConfig(
                    inactivity_timeout=inactivity_duration
                )
            )

        # B. Global Variables
        if not self.deployment_state.get("vars_deployed") or is_update_pass:
            vars_list = list(self.ir.parameters.values())
            if vars_list:
                app_updates["variable_declarations"] = vars_list

        # C. Global App Model Settings (WAI for CXAS model compatibility)
        if (
            not self.deployment_state.get("app_model_configured")
            or is_update_pass
        ):
            default_model = self.ir.metadata.default_model or "gemini-2.5-flash"
            app_updates["model_settings"] = types.ModelSettings(
                model=default_model
            )

        # D. Execute Unified App Update Call
        if app_updates:
            logger.info(
                "\nApplying Consolidated App Configurations (Silence "
                "Timeout, Global Variables, Model Settings)..."
            )
            try:
                self.ps_apps.update_app(full_app_name, **app_updates)
                logger.info(
                    "   -> App Configurations deployed successfully in a "
                    "single transaction!"
                )
                if "audio_processing_config" in app_updates:
                    self.deployment_state["app_timeout_configured"] = True
                if "variable_declarations" in app_updates:
                    self.deployment_state["vars_deployed"] = True
                if "model_settings" in app_updates:
                    self.deployment_state["app_model_configured"] = True
            except Exception as e:
                logger.error(
                    f"❌ Failed to deploy consolidated App configurations: {e}"
                )
                # Graceful single-field fallback to prevent complete blocking
                if "audio_processing_config" in app_updates:
                    try:
                        self.ps_apps.update_app(
                            full_app_name,
                            audio_processing_config=app_updates[
                                "audio_processing_config"
                            ],
                        )
                        self.deployment_state["app_timeout_configured"] = True
                    except Exception:
                        pass
                if "variable_declarations" in app_updates:
                    try:
                        self.ps_apps.update_app(
                            full_app_name,
                            variable_declarations=app_updates[
                                "variable_declarations"
                            ],
                        )
                        self.deployment_state["vars_deployed"] = True
                    except Exception:
                        pass
                if "model_settings" in app_updates:
                    try:
                        self.ps_apps.update_app(
                            full_app_name,
                            model_settings=app_updates["model_settings"],
                        )
                        self.deployment_state["app_model_configured"] = True
                    except Exception:
                        pass

        # 3. Deploy Tools
        pending_tools = [
            tool
            for tool in self.ir.tools.values()
            if tool.status != MigrationStatus.DEPLOYED
        ]

        if pending_tools:
            logger.info(f"\nDeploying {len(pending_tools)} Tools & Toolsets...")
            for tool in pending_tools:
                res_type = tool.type
                payload = tool.payload
                tool_id = tool.id
                display_name = payload.get("displayName") or payload.get(
                    "display_name", tool_id
                )

                try:
                    if res_type == "TOOLSET":
                        logger.info(f"  Creating Toolset: '{display_name}'...")
                        new_res = self.ps_tools.create_tool(
                            tool_id=tool_id,
                            display_name=display_name,
                            payload=payload["open_api_toolset"],
                            tool_type="open_api_toolset",
                            description=payload.get("description", ""),
                        )
                    elif res_type == "PYTHON":
                        logger.info(
                            f"  Creating Python Tool: '{display_name}'..."
                        )
                        new_res = self.ps_tools.create_tool(
                            tool_id=tool_id,
                            display_name=display_name,
                            payload=payload.get("pythonFunction", {}),
                            tool_type="python_function",
                            description=payload.get("description", ""),
                        )
                    else:
                        logger.info(
                            f"  Creating Data Store Tool: '{display_name}'..."
                        )
                        new_res = self.ps_tools.create_tool(
                            tool_id=tool_id,
                            display_name=display_name,
                            payload=payload.get("data_store_tool", {}),
                            tool_type="data_store_tool",
                            description=payload.get("description", ""),
                        )
                except Exception as e:
                    if is_update_pass and (
                        "409" in str(e) or "Already exists" in str(e)
                    ):
                        logger.info(
                            f"    -> Tool '{display_name}' already exists. "
                            "Updating instead..."
                        )
                        try:
                            # Update expects full resource name, construct it
                            full_tool_name = (
                                f"{full_app_name}/toolsets/{tool_id}"
                                if res_type == "TOOLSET"
                                else f"{full_app_name}/tools/{tool_id}"
                            )
                            if res_type == "TOOLSET":
                                new_res = self.ps_tools.update_tool(
                                    tool_name=full_tool_name,
                                    display_name=display_name,
                                    open_api_toolset=payload[
                                        "open_api_toolset"
                                    ],
                                    description=payload.get("description", ""),
                                )
                            elif res_type == "PYTHON":
                                new_res = self.ps_tools.update_tool(
                                    tool_name=full_tool_name,
                                    display_name=display_name,
                                    python_function=payload.get(
                                        "pythonFunction", {}
                                    ),
                                )
                            else:
                                new_res = self.ps_tools.update_tool(
                                    tool_name=full_tool_name,
                                    display_name=display_name,
                                    data_store_tool=payload.get(
                                        "data_store_tool", {}
                                    ),
                                )
                        except Exception as update_e:
                            logger.error(
                                f"    -> Exception updating {res_type} "
                                f"'{display_name}': {update_e}"
                            )
                            tool.status = MigrationStatus.FAILED
                            continue
                    else:
                        logger.error(
                            f"    -> Exception creating {res_type} "
                            f"'{display_name}': {e}"
                        )
                        tool.status = MigrationStatus.FAILED
                        continue

                if new_res and hasattr(new_res, "name"):
                    tool.status = MigrationStatus.DEPLOYED
                    self.reporter.log_tool(res_type, display_name, new_res.name)
                else:
                    logger.error(
                        f"    -> Failed to deploy {res_type} '{display_name}'."
                    )
                    tool.status = MigrationStatus.FAILED

    @staticmethod
    def _fix_agent_ref(match, valid_display_names):
        raw_name = match.group(1).strip()
        if raw_name.upper() in ["END_SESSION", "END_FLOW"]:
            return match.group(0)

        # Remove underscores/hyphens generated by LLM to match the clean
        # display name
        normalized_name = re.sub(r"[_\\-]+", " ", raw_name).strip().lower()

        if normalized_name in valid_display_names:
            exact_name = valid_display_names[normalized_name]
            return f"{{@AGENT: {exact_name}}}"

        # Fallback: Just return it with underscores replaced by spaces
        # so CXAS routing doesn't break
        fallback_name = re.sub(r"[_]+", " ", raw_name).strip()
        return f"{{@AGENT: {fallback_name}}}"

    async def _deploy_pending_agents(self, is_update_pass: bool = False):
        """Deploys any agents in the IR that have been compiled but not yet
        deployed.
        """
        full_app_name = self.ir.metadata.app_resource_name
        default_model = self.ir.metadata.default_model

        pending_agents = [
            agent
            for agent in self.ir.agents.values()
            if agent.status
            in [MigrationStatus.COMPILED, MigrationStatus.GENERATED]
        ]

        if pending_agents:
            logger.info(f"\nDeploying {len(pending_agents)} Agents...")

            # --- Build a robust map of all actual Agent Display Names ---
            valid_display_names = {
                re.sub(r"[_\\-]+", " ", agent.display_name)
                .strip()
                .lower(): agent.display_name
                for agent in self.ir.agents.values()
            }

            for agent in pending_agents:
                display_name = agent.display_name
                logger.info(
                    f"  Deploying Agent: '{display_name}' ({agent.type})..."
                )

                # Format Callbacks if they exist
                callback_payload = {}
                cb_dict = agent.callbacks or {}

                # Auto-inject universal system directives handler into
                # before_model_callback
                used_directives = set()
                requires_end_session = False
                tool_names_with_directives = set()
                for t_ref in agent.tools:
                    tool_id = t_ref.split("/")[-1]
                    tool = self.ir.tools.get(tool_id)
                    if not tool:
                        for t in self.ir.tools.values():
                            if t.name == t_ref:
                                tool = t
                                break

                    if tool and tool.type == "PYTHON":
                        code = tool.payload.get("pythonFunction", {}).get(
                            "python_code", ""
                        )
                        tool_display_name = tool.payload.get(
                            "displayName", tool_id
                        )
                        for directive in [
                            "respond",
                            "add_override",
                            "done",
                            "fail",
                            "cancel",
                            "escalate",
                            "agentTransfer",
                        ]:
                            if re.search(
                                rf"['\"]action['\"]\s*:\s*['\"]{directive}['\"]",
                                code,
                            ):
                                used_directives.add(directive)
                                tool_names_with_directives.add(
                                    tool_display_name
                                )

                # Callback-driven system 'end_session' check
                if used_directives.intersection(
                    {
                        "done",
                        "fail",
                        "cancel",
                        "escalate",
                        "agentTransfer",
                        "add_override",
                    }
                ):
                    requires_end_session = True

                system_directive_snippet = ""
                if used_directives and tool_names_with_directives:
                    directive_blocks = []
                    if "respond" in used_directives:
                        directive_blocks.append(
                            'if action == "respond":\n'
                            '    text = directive.get("text", "")\n'
                            "    parts_to_return.append(\n"
                            "        Part.from_text(text=text)\n"
                            "    )"
                        )
                    if "add_override" in used_directives:
                        directive_blocks.append(
                            'elif action == "add_override":\n'
                            '    t_raw = str(directive.get("target", ""))\n'
                            '    target = t_raw.split(".")[-1]\n'
                            '    params = directive.get("parameters", {})\n'
                            "    if isinstance(params, dict):\n"
                            "        for k, v in params.items():\n"
                            "            callback_context.variables[k] = v\n"
                            '            print(f"Injected routing: {k}={v}")\n'
                            '    print(f"Executing add_override: {target}")\n'
                            '    if target in ["agentTransfer", "Transfer"]:\n'
                            "        parts_to_return.append(\n"
                            "            Part.from_end_session(\n"
                            '                reason="escalate_to_human",\n'
                            "                escalated=True,\n"
                            "            )\n"
                            "        )\n"
                            "    else:\n"
                            "        parts_to_return.append(\n"
                            "            Part.from_agent_transfer(\n"
                            "                agent=target\n"
                            "            )\n"
                            "        )"
                        )
                    if "done" in used_directives:
                        directive_blocks.append(
                            'elif action == "done":\n'
                            "    parts_to_return.append(\n"
                            "        Part.from_end_session(\n"
                            '            reason="success"\n'
                            "        )\n"
                            "    )"
                        )
                    if "fail" in used_directives:
                        directive_blocks.append(
                            'elif action == "fail":\n'
                            "    parts_to_return.append(\n"
                            "        Part.from_end_session(\n"
                            '            reason="failure"\n'
                            "        )\n"
                            "    )"
                        )
                    if "cancel" in used_directives:
                        directive_blocks.append(
                            'elif action == "cancel":\n'
                            "    parts_to_return.append(\n"
                            "        Part.from_end_session(\n"
                            '            reason="cancelled"\n'
                            "        )\n"
                            "    )"
                        )
                    if "escalate" in used_directives:
                        directive_blocks.append(
                            'elif action == "escalate":\n'
                            "    parts_to_return.append(\n"
                            "        Part.from_end_session(\n"
                            '            reason="escalated",\n'
                            "            escalated=True,\n"
                            "        )\n"
                            "    )"
                        )
                    if "agentTransfer" in used_directives:
                        directive_blocks.append(
                            'elif action == "agentTransfer":\n'
                            '    target = directive.get("target", "")\n'
                            "    if target:\n"
                            "        parts_to_return.append(\n"
                            "            Part.from_agent_transfer(\n"
                            "                agent=target\n"
                            "            )\n"
                            "        )\n"
                            "    else:\n"
                            "        parts_to_return.append(\n"
                            "            Part.from_end_session(\n"
                            '                reason="escalated",\n'
                            "                escalated=True,\n"
                            "            )\n"
                            "        )"
                        )

                    raw_blocks = "\n".join(directive_blocks)
                    indented_lines = [
                        ("                            " + line if line else "")
                        for line in raw_blocks.split("\n")
                    ]
                    directive_blocks = indented_lines

                    combined_blocks = "\n".join(directive_blocks)
                    combined_blocks = combined_blocks.replace(
                        "                            elif",
                        "                            if",
                        1,
                    )

                    # Safely inject the tool names into the any() check
                    tool_names_formatted = ", ".join(
                        [f'"{name}"' for name in tool_names_with_directives]
                    )

                    system_directive_snippet = f"""
    # --- MIGRATION AUTO-GENERATED: SYSTEM DIRECTIVES ---
    if llm_request.contents and llm_request.contents[-1].parts:
        for part in llm_request.contents[-1].parts:
            if any(
                part.has_function_response(t)
                for t in [{tool_names_formatted}]
            ):
                if part.function_response and hasattr(
                    part.function_response, "response"
                ):
                    response_data = part.function_response.response

                    directives = []
                    if isinstance(response_data, dict):
                        if "__cxas_system_directives__" in response_data:
                            directives = response_data[
                                "__cxas_system_directives__"
                            ]
                        elif (
                            "result" in response_data
                            and isinstance(response_data["result"], dict)
                            and "__cxas_system_directives__"
                            in response_data["result"]
                        ):
                            directives = response_data["result"][
                                "__cxas_system_directives__"
                            ]

                    if directives:
                        parts_to_return = []
                        for directive in directives:
                            action = directive.get("action")
{combined_blocks}
                        if parts_to_return:
                            return LlmResponse.from_parts(parts=parts_to_return)
"""

                if system_directive_snippet:
                    existing_bmc = cb_dict.get("before_model_callback", "")
                    if "def before_model_callback" in existing_bmc:
                        # Inject right after the function definition
                        new_bmc = re.sub(
                            r"(def before_model_callback[^\n]*:)",
                            r"\1" + system_directive_snippet,
                            existing_bmc,
                            count=1,
                        )
                        cb_dict["before_model_callback"] = new_bmc
                    else:
                        # Create the function from scratch
                        new_bmc = (
                            "def before_model_callback(\n"
                            "    callback_context: CallbackContext,\n"
                            "    llm_request: LlmRequest,\n"
                            ") -> Optional[LlmResponse]:\n"
                            + system_directive_snippet
                            + "\n    return None\n"
                        )
                        cb_dict["before_model_callback"] = (
                            new_bmc + "\n" + existing_bmc
                        )

                for cb_type, cb_code in cb_dict.items():
                    if cb_code:
                        key = cb_type + "s"
                        callback_payload[key] = [{"python_code": cb_code}]

                # --- Clean Instruction Syntax & Agent Names ---
                instruction = agent.instruction

                instruction = re.sub(
                    r"{@AGENT:\s*([^}]+)}",
                    lambda m: MigrationService._fix_agent_ref(
                        m, valid_display_names
                    ),
                    instruction,
                )
                # Save corrected instruction back to IR for the linker
                agent.instruction = instruction

                # Map local IR tool IDs to full resource paths for the API
                resolved_tools = []
                deployed_tool_names = {
                    tool.name
                    for tool in self.ir.tools.values()
                    if tool.status == MigrationStatus.DEPLOYED
                }

                # --- Attach system 'end_session' tool ---
                end_session_resource = f"{full_app_name}/tools/end_session"
                if requires_end_session or re.search(
                    r"{@TOOL:\s*end_session\s*}", instruction, re.IGNORECASE
                ):
                    if end_session_resource not in resolved_tools:
                        resolved_tools.append(end_session_resource)
                        logger.info(
                            "    - Automatically attached 'end_session' "
                            "system tool driven by active "
                            "callback/instruction requirements."
                        )

                # --- Attach system 'set_session_variables' tool ---
                set_vars_resource = (
                    f"{full_app_name}/tools/set_session_variables"
                )
                if re.search(
                    r"{@TOOL:\s*set_session_variables\s*}",
                    instruction,
                    re.IGNORECASE,
                ):
                    if set_vars_resource not in resolved_tools:
                        resolved_tools.append(set_vars_resource)
                        logger.info(
                            "    - Detected 'set_session_variables' in "
                            "instructions. Attached system tool "
                            "automatically."
                        )

                for t_ref in agent.tools:
                    if t_ref in ("end_session", end_session_resource):
                        if end_session_resource not in resolved_tools:
                            resolved_tools.append(end_session_resource)
                        continue

                    if t_ref in ("set_session_variables", set_vars_resource):
                        if set_vars_resource not in resolved_tools:
                            resolved_tools.append(set_vars_resource)
                        continue

                    if t_ref.startswith("projects/"):
                        if t_ref in deployed_tool_names:
                            resolved_tools.append(t_ref)
                        else:
                            logger.warning(
                                f"⚠️ Omitting tool {t_ref.split('/')[-1]} "
                                f"from agent '{display_name}' "
                                "because it failed to deploy."
                            )
                    elif t_ref in self.ir.tools:
                        if (
                            self.ir.tools[t_ref].status
                            == MigrationStatus.DEPLOYED
                        ):
                            resolved_tools.append(self.ir.tools[t_ref].name)
                        else:
                            logger.warning(
                                f"⚠️ Omitting tool {t_ref} "
                                f"from agent '{display_name}' "
                                "because it failed to deploy."
                            )
                    else:
                        logger.warning(
                            f"⚠️ Could not resolve tool reference for "
                            f"agent '{display_name}': "
                            f"{t_ref}"
                        )

                resolved_toolsets = []
                for ts in agent.toolsets:
                    ts_copy = ts.copy()
                    ts_name = ts_copy["toolset"]

                    if not ts_name.startswith("projects/"):
                        if (
                            ts_name in self.ir.tools
                            and self.ir.tools[ts_name].status
                            == MigrationStatus.DEPLOYED
                        ):
                            ts_copy["toolset"] = self.ir.tools[ts_name].name
                            resolved_toolsets.append(ts_copy)
                        else:
                            logger.warning(
                                f"⚠️ Omitting toolset {ts_name} "
                                f"from agent '{display_name}' "
                                "because it failed to deploy."
                            )
                    elif ts_name in deployed_tool_names:
                        resolved_toolsets.append(ts_copy)
                    else:
                        logger.warning(
                            f"⚠️ Omitting toolset {ts_name.split('/')[-1]} "
                            f"from agent '{display_name}' "
                            "because it failed to deploy."
                        )

                ps_agent_payload = {
                    "display_name": display_name,
                    "description": agent.description
                    or (
                        agent.blueprint.get("agent_metadata", {}).get("role")
                        if agent.blueprint
                        else None
                    )
                    or agent.type,
                    "instruction": agent.instruction,
                    "tools": list(set(resolved_tools)),
                    "toolsets": resolved_toolsets,
                    "model_settings": agent.model_settings
                    or {"model": default_model},
                }
                ps_agent_payload.update(callback_payload)

                ps_agent_payload.pop("display_name", None)
                model_to_use = default_model
                if "model_settings" in ps_agent_payload:
                    ms = ps_agent_payload.pop("model_settings")
                    if isinstance(ms, dict) and "model" in ms:
                        model_to_use = ms["model"]
                    elif hasattr(ms, "model"):
                        model_to_use = ms.model

                try:
                    new_ps_agent = self.ps_agents.create_agent(
                        display_name=display_name,
                        model=model_to_use,
                        **ps_agent_payload,
                    )
                except Exception as e:
                    if is_update_pass and (
                        "409" in str(e) or "Already exists" in str(e)
                    ):
                        logger.info(
                            f"    -> Agent '{display_name}' already exists. "
                            "Updating instead..."
                        )
                        try:
                            # In cxas_scrapi.core.agents.py, update_agent
                            # takes agent_name (full resource name)
                            # Let's find the resource name from existing app
                            existing_agent_map = self.ps_agents.get_agents_map(
                                reverse=True
                            )
                            if display_name in existing_agent_map:
                                agent_resource_name = existing_agent_map[
                                    display_name
                                ]
                                update_payload = ps_agent_payload.copy()
                                update_payload["model_settings"] = {
                                    "model": model_to_use
                                }
                                new_ps_agent = self.ps_agents.update_agent(
                                    agent_name=agent_resource_name,
                                    **update_payload,
                                )
                            else:
                                logger.error(
                                    f"    -> Agent '{display_name}' "
                                    "returned 409 but wasn't found in "
                                    "get_agents_map()."
                                )
                                continue
                        except Exception as update_e:
                            logger.error(
                                f"    -> Exception updating Agent "
                                f"'{display_name}': {update_e}"
                            )
                            continue
                    else:
                        logger.error(
                            f"    -> Exception creating Agent "
                            f"'{display_name}': {e}"
                        )
                        continue

                if new_ps_agent and hasattr(new_ps_agent, "name"):
                    logger.info("    -> Success!")
                    agent.status = MigrationStatus.DEPLOYED
                    # Save deployed API name for linking
                    agent.resource_name = new_ps_agent.name
                    self.reporter.log_agent(
                        display_name,
                        new_ps_agent.name,
                        ps_agent_payload["description"],
                        default_model,
                    )
                else:
                    logger.error(
                        f"    -> Failed to deploy Agent '{display_name}'."
                    )

    def _sanitize_resource_id(
        self, resource_id: str, min_len: int = 5, max_len: int = 36
    ) -> str:
        """Sanitizes a string to be a valid CXAS resource ID.

        Regex requirement: [a-zA-Z0-9][a-zA-Z0-9-_]{4,35}
        """
        # Replace spaces and other invalid characters (including dots)
        # with underscores
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", resource_id)

        # Ensure it starts with a letter or underscore (CXAS prefers
        # alphanumeric start)
        # We strip leading special chars to be safe
        sanitized = sanitized.lstrip("_-")

        # If it's empty or still doesn't start with a letter/number,
        # prepend 'tool_'
        if not sanitized or not re.match(r"^[a-zA-Z0-9]", sanitized):
            sanitized = "tool_" + sanitized

        # Truncate to max length
        sanitized = sanitized[:max_len]

        # Pad to min length if necessary
        while len(sanitized) < min_len:
            sanitized += "_"

        return sanitized

    def _sanitize_display_name(
        self, display_name: str, max_len: int = 85
    ) -> str:
        """Sanitizes a display name for CXAS resources."""
        # Per error message, allow alphanumeric, single spaces, dashes,
        # underscores.
        # Remove any other characters.
        sanitized = re.sub(r"[^a-zA-Z0-9_ -]", "", display_name)
        # Collapse consecutive spaces, dashes, or underscores into a
        # single space
        sanitized = re.sub(r"[ _-]+", " ", sanitized).strip()
        # Truncate to max length
        return sanitized[:max_len]

    def _preprocess_text_fields(self, data_structure: any) -> any:
        """Recursively traverses a data structure (dict or list) and replaces

        'playbook' with 'agent' in all string values. This is done in-place.
        """
        if isinstance(data_structure, dict):
            for key, value in data_structure.items():
                if isinstance(value, str):
                    # Apply replacement to string values
                    data_structure[key] = re.sub(
                        r"playbook", "agent", value, flags=re.IGNORECASE
                    )
                else:
                    # Recurse into nested structures
                    self._preprocess_text_fields(value)
        elif isinstance(data_structure, list):
            for i, item in enumerate(data_structure):
                if isinstance(item, str):
                    # Apply replacement to string items in a list
                    data_structure[i] = re.sub(
                        r"playbook", "agent", item, flags=re.IGNORECASE
                    )
                else:
                    # Recurse into nested structures
                    self._preprocess_text_fields(item)
        elif hasattr(data_structure.__class__, "model_fields"):
            for key in data_structure.__class__.model_fields.keys():
                val = getattr(data_structure, key)
                if isinstance(val, str):
                    setattr(
                        data_structure,
                        key,
                        re.sub(r"playbook", "agent", val, flags=re.IGNORECASE),
                    )
                else:
                    self._preprocess_text_fields(val)
        return data_structure

    async def _process_single_flow(
        self,
        flow_wrapper: Dict[str, Any],
        target_app_resource_name: str,
        parameter_name_map: Dict[str, str],
    ):
        """Processes a single DFCX flow: resolves dependencies, visualizes,
        generates instructions and tools, and deploys them.
        """
        flow_name = flow_wrapper.flow_data.get("displayName", "Unnamed")

        logger.info(f"[{flow_name}] Starting processing...")

        resolver = FlowDependencyResolver(self.source_agent_data)
        context_data = resolver.resolve(flow_wrapper)
        viz = FlowTreeVisualizer(context_data)

        buf = io.StringIO()
        console = Console(file=buf)
        console.print(viz.build_tree())
        tree_view = buf.getvalue()

        # Step 2A: Architecture Expert Blueprinting
        blueprint_2a = await self.designer.run_step_2a(
            flow_name=flow_name, tree_view=tree_view, target_ir=self.ir
        )

        if "error" not in blueprint_2a:
            logger.info(
                f"[{flow_name}] Launching 2B (Instructions) and "
                "2C (Tools) concurrently..."
            )
            task_2b = self.designer.run_step_2b_instructions(
                flow_name, blueprint_2a, tree_view
            )
            task_2c = self.designer.run_step_2c_tools_and_callbacks(
                flow_name, blueprint_2a, tree_view, target_ir=self.ir
            )

            instructions_xml, tools_callbacks_data = await asyncio.gather(
                task_2b, task_2c
            )

            # --- DETERMINISTIC VARIABLE NAME SANITIZATION IN TOOLS & ---
            # --- CALLBACKS ---
            python_var_pattern = re.compile(
                r"(get_variable\s*\(\s*|set_variable\s*\(\s*|"
                r"(?:state|variables|payload|kwargs)"
                r"(?:\.get\s*\(\s*|\.set\s*\(\s*|\s*\[\s*))"
                r"([\'\"])([a-zA-Z0-9_-]+)([\'\"])"
            )

            def flow_python_var_replacer(match):
                prefix = match.group(1)
                quote = match.group(2)
                var_name = match.group(3)
                closing_quote = match.group(4)

                sanitized_name = parameter_name_map.get(var_name)
                if not sanitized_name:
                    sanitized_name = re.sub(r"[^a-zA-Z0-9_]", "_", var_name)

                return f"{prefix}{quote}{sanitized_name}{closing_quote}"

            # 1. Sanitize generated tools
            for tool in tools_callbacks_data.get("tools", []):
                tool_code = tool.get("code", "")
                if tool_code:
                    tool_code = python_var_pattern.sub(
                        flow_python_var_replacer, tool_code
                    )
                    tool["code"] = tool_code

            # 2. Sanitize generated callbacks
            for cb_type, cb_code in list(
                tools_callbacks_data.get("callbacks", {}).items()
            ):
                if cb_code:
                    new_cb_code = python_var_pattern.sub(
                        flow_python_var_replacer, cb_code
                    )
                    tools_callbacks_data["callbacks"][cb_type] = new_cb_code

            # Robustly extract the description
            agent_meta = blueprint_2a.get("agent_metadata", {})
            flow_description = (
                agent_meta.get("role")
                or agent_meta.get("primary_goal")
                or f"Migrated Flow: {flow_name}"
            )

            self.ir.agents[flow_name] = IRAgent(
                type="FLOW",
                display_name=self._sanitize_display_name(flow_name),
                description=flow_description,
                instruction=instructions_xml,
                blueprint=blueprint_2a,
                callbacks=tools_callbacks_data.get("callbacks", {}),
                tools=[],
                toolsets=[],
                status=MigrationStatus.COMPILED,
            )

            # 1. Store & IMMEDIATELY DEPLOY Generated Python Tools
            for tool in tools_callbacks_data.get("tools", []):
                tool_name = tool.get("name")
                safe_tool_id = self._sanitize_resource_id(tool_name)
                full_tool_name = (
                    f"{target_app_resource_name}/tools/{safe_tool_id}"
                )

                tool_payload = {
                    "name": safe_tool_id,
                    "displayName": tool_name,
                    "pythonFunction": {
                        "name": tool_name,
                        "description": tool.get("description", ""),
                        "python_code": tool.get("code", ""),
                    },
                }

                self.ir.tools[safe_tool_id] = IRTool(
                    type="PYTHON",
                    id=safe_tool_id,
                    name=full_tool_name,
                    payload=tool_payload,
                    status=MigrationStatus.COMPILED,
                )

                self.ir.agents[flow_name].tools.append(full_tool_name)

                # DEPLOY THE TOOL NOW
                logger.info(
                    f"[{flow_name}] Deploying generated Python tool: "
                    f"{safe_tool_id}"
                )
                try:
                    created_tool = self.ps_tools.create_tool(
                        tool_id=safe_tool_id,
                        display_name=tool_name,
                        payload=tool_payload["pythonFunction"],
                        tool_type="python_function",
                    )
                    if created_tool:
                        self.ir.tools[
                            safe_tool_id
                        ].status = MigrationStatus.DEPLOYED
                except Exception as e:
                    logger.error(
                        f"[{flow_name}] Failed to deploy tool "
                        f"{safe_tool_id}: {e}"
                    )

            # 1.5 MISSING LOGIC RESTORATION
            valid_display_names = {
                re.sub(r"[_\\-]+", " ", agent.display_name)
                .strip()
                .lower(): agent.display_name
                for agent in self.ir.agents.values()
            }

            instructions_xml = re.sub(
                r"{@AGENT:\s*([^}]+)}",
                lambda m: MigrationService._fix_agent_ref(
                    m, valid_display_names
                ),
                instructions_xml,
            )

            # 1.6 DFCX TO CXAS VARIABLE MAPPINGS ALIGNMENT
            var_pattern = re.compile(
                r"\{([a-zA-Z0-9_-]+)\}|"
                r"`\$(?:(?:session|page)\.params\.)?([a-zA-Z0-9_-]+)`|"
                r"\$\`(?:(?:session|page)\.params\.)?([a-zA-Z0-9_-]+)\`|"
                r"\$\{(?:(?:session|page)\.params\.)?([a-zA-Z0-9_-]+)\}|"
                r"\$(?:(?:session|page)\.params\.)?([a-zA-Z0-9_-]+)"
            )

            def flow_var_replacer(match):
                original_match = match.group(0)
                var_name = next(g for g in match.groups() if g is not None)

                sanitized_name = parameter_name_map.get(var_name)
                if not sanitized_name:
                    sanitized_name = re.sub(r"[^a-zA-Z0-9_]", "_", var_name)

                new_ref = f"{{{sanitized_name}}}"
                self.reporter.log_transformation(
                    "Variable Syntax",
                    original_match,
                    new_ref,
                    "Updated DFCX variable reference in Flow "
                    "instructions to CXAS {} format",
                )
                return new_ref

            instructions_xml = var_pattern.sub(
                flow_var_replacer, instructions_xml
            )
            self.ir.agents[flow_name].instruction = instructions_xml

            # B. Attach System end_session Tool
            end_session_res = f"{target_app_resource_name}/tools/end_session"
            if re.search(
                r"{@TOOL:\s*end_session\s*}", instructions_xml, re.IGNORECASE
            ):
                if end_session_res not in self.ir.agents[flow_name].tools:
                    self.ir.agents[flow_name].tools.append(end_session_res)
                    logger.info(
                        f"[{flow_name}] Attached system tool: end_session"
                    )

            # C. Attach Standard Tools/Toolsets referenced directly in XML
            xml_tools = re.findall(r"{@TOOL:\s*([^}]+)}", instructions_xml)
            for t_name in xml_tools:
                t_clean = t_name.strip()
                if t_clean == "end_session":
                    continue

                matched_tool = next(
                    (
                        tool
                        for tool in self.ir.tools.values()
                        if tool.payload.get("displayName") == t_clean
                        or tool.id == t_clean
                    ),
                    None,
                )
                if matched_tool:
                    if (
                        matched_tool.type == "TOOL"
                        and matched_tool.name
                        not in self.ir.agents[flow_name].tools
                    ):
                        self.ir.agents[flow_name].tools.append(
                            matched_tool.name
                        )
                    elif matched_tool.type == "TOOLSET":
                        ts_entry = {"toolset": matched_tool.name}
                        if ts_entry not in self.ir.agents[flow_name].toolsets:
                            self.ir.agents[flow_name].toolsets.append(ts_entry)

            # D. Attach OpenAPI Toolsets required by the Python Wrapper Code
            for py_tool in tools_callbacks_data.get("tools", []):
                py_code = py_tool.get("code", "")
                used_ops = re.findall(r"tools\.([a-zA-Z0-9_]+)", py_code)
                for op in used_ops:
                    for tool in self.ir.tools.values():
                        if tool.type == "TOOLSET" and op in tool.operation_ids:
                            ts_entry = {"toolset": tool.name}
                            if (
                                ts_entry
                                not in self.ir.agents[flow_name].toolsets
                            ):
                                self.ir.agents[flow_name].toolsets.append(
                                    ts_entry
                                )
                                logger.info(
                                    f"[{flow_name}] Attached backend toolset "
                                    "dependency: "
                                    f"{tool.payload.get('displayName', op)}"
                                )

            # E. DEPLOY THE AGENT (Missing Logic Restoration)
            logger.info(f"[{flow_name}] Deploying agent...")

            # Format Callbacks if they exist
            callback_payload = {}
            for cb_type, cb_code in tools_callbacks_data.get(
                "callbacks", {}
            ).items():
                if cb_code:
                    key = cb_type + "s"
                    callback_payload[key] = [{"python_code": cb_code}]

            display_name = self.ir.agents[flow_name].display_name
            agent_payload = {
                "description": self.ir.agents[flow_name].description,
                "instruction": self.ir.agents[flow_name].instruction,
                "tools": self.ir.agents[flow_name].tools,
                "toolsets": self.ir.agents[flow_name].toolsets,
                "model_settings": {"model": self.ir.metadata.default_model},
            }
            agent_payload.update(callback_payload)

            try:
                new_agent = self.ps_agents.create_agent(
                    display_name=display_name,
                    model=self.ir.metadata.default_model,
                    **agent_payload,
                )
                if new_agent and hasattr(new_agent, "name"):
                    logger.info(f"[{flow_name}] -> Success! Deployed Agent.")
                    self.ir.agents[flow_name].status = MigrationStatus.DEPLOYED
                    self.ir.agents[flow_name].resource_name = new_agent.name
                    self.reporter.log_agent(
                        flow_name,
                        new_agent.name,
                        agent_payload["description"],
                        self.default_model,
                    )
                else:
                    logger.error(f"[{flow_name}] ❌ Failed to deploy agent.")
            except Exception as e:
                logger.error(
                    f"[{flow_name}] ❌ API Exception during agent "
                    f"deployment: {e}"
                )
        else:
            logger.error(f"[{flow_name}] Failed to generate blueprint.")
