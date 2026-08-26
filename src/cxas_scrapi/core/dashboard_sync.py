"""Declarative YAML sync and diff engine for CCAI dashboards."""

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

from __future__ import annotations

import difflib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from cxas_scrapi.core.insights import Insights

logger = logging.getLogger(__name__)

VALID_VISUALIZATION_TYPES = {
    "CHART_VISUALIZATION_TYPE_UNSPECIFIED",
    "BAR",
    "LINE",
    "AREA",
    "PIE",
    "SCATTER",
    "TABLE",
    "SCORE_CARD",
    "SUNBURST",
    "GAUGE",
    "SANKEY",
}

VALID_TIME_UNITS = {
    "TIME_UNIT_UNSPECIFIED",
    "DAY",
    "WEEK",
    "MONTH",
    "QUARTER",
    "YEAR",
}


def _validate_date_range(
    dr: Any, context: str, errors: list[str]
) -> None:
    """Validates date range config structure."""
    if not isinstance(dr, dict):
        errors.append(f"{context}: 'date_range' must be a dictionary.")
        return

    rel = dr.get("relative") or dr.get("relativeDateRange")
    abs_range = dr.get("absolute") or dr.get("absoluteDateRange")

    if not rel and not abs_range:
        errors.append(
            f"{context}: 'date_range' must specify either 'relative' or "
            "'absolute'."
        )
        return

    if rel:
        if not isinstance(rel, dict):
            errors.append(
                f"{context}: 'relative' date range must be a dictionary."
            )
        else:
            unit = str(rel.get("unit", "")).upper()
            if unit and unit not in VALID_TIME_UNITS:
                errors.append(
                    f"{context}: Invalid relative time unit '{unit}'. "
                    f"Must be one of {sorted(VALID_TIME_UNITS)}."
                )


def _validate_chart(
    chart: Any, context: str, errors: list[str]
) -> None:
    """Validates chart dictionary structure."""
    if not isinstance(chart, dict):
        errors.append(f"{context}: Chart must be a dictionary.")
        return

    vis_type = (
        chart.get("chart_visualization_type")
        or chart.get("chartVisualizationType")
        or chart.get("visualization_type")
    )
    if vis_type:
        vis_upper = str(vis_type).upper()
        if vis_upper not in VALID_VISUALIZATION_TYPES:
            errors.append(
                f"{context}: Invalid chart_visualization_type '{vis_type}'. "
                f"Must be one of {sorted(VALID_VISUALIZATION_TYPES)}."
            )

    data_source = chart.get("data_source") or chart.get("dataSource")
    if data_source and not isinstance(data_source, dict):
        errors.append(f"{context}: 'data_source' must be a dictionary.")


def _validate_container(
    container: Any, context: str, is_root: bool, errors: list[str]
) -> None:
    """Recursively validates a container and its widgets."""
    if not isinstance(container, dict):
        errors.append(f"{context}: Container must be a dictionary.")
        return

    disp_name = container.get("display_name") or container.get("displayName")
    if disp_name and len(str(disp_name)) > 100:
        errors.append(
            f"{context}: Container displayName must not exceed 100 characters."
        )

    date_range = (
        container.get("date_range")
        if "date_range" in container
        else container.get("dateRangeConfig")
    )
    if date_range is not None:
        _validate_date_range(date_range, context, errors)

    widgets = container.get("widgets")
    if widgets is not None and not isinstance(widgets, list):
        errors.append(f"{context}: 'widgets' must be a list.")
        return

    if is_root:
        if not widgets:
            errors.append(
                f"{context}: Root container must contain at least one tab."
            )
            return

        # CCAI Constraint: direct widgets in root container must be Containers
        for idx, w in enumerate(widgets):
            if not isinstance(w, dict) or (
                "container" not in w and "Container" not in w
            ):
                errors.append(
                    f"{context}: The widgets in the root container must be of "
                    f"type Container (widget #{idx})."
                )
            else:
                sub_c = w.get("container") or w.get("Container")
                _validate_container(
                    sub_c,
                    f"{context} -> Tab #{idx}",
                    is_root=False,
                    errors=errors,
                )
    else:
        for idx, w in enumerate(widgets or []):
            if not isinstance(w, dict):
                errors.append(f"{context}: Widget #{idx} must be a dictionary.")
                continue

            if "container" in w or "Container" in w:
                sub_c = w.get("container") or w.get("Container")
                _validate_container(
                    sub_c,
                    f"{context} -> NestedContainer #{idx}",
                    is_root=False,
                    errors=errors,
                )
            elif "chart" in w or "Chart" in w:
                chart = w.get("chart") or w.get("Chart")
                _validate_chart(
                    chart, f"{context} -> Chart #{idx}", errors
                )
            elif "chart_reference" in w or "chartReference" in w:
                ref = w.get("chart_reference") or w.get("chartReference")
                if not isinstance(ref, str) or not ref:
                    errors.append(
                        f"{context}: Widget #{idx} 'chart_reference' must be "
                        "a non-empty string."
                    )
            else:
                errors.append(
                    f"{context}: Widget #{idx} must specify 'container', "
                    "'chart', or 'chart_reference'."
                )


def validate_dashboards_dict(data: dict[str, Any]) -> list[str]:
    """Validates dashboards YAML dict against CCAI Insights requirements.

    Args:
        data: The dictionary structure loaded from YAML.

    Returns:
        A list of validation error strings. Returns empty list if valid.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["Root content must be a mapping/dictionary."]

    dashboards = data.get("dashboards")
    # If no 'dashboards' key, check if root itself defines a single dashboard
    if dashboards is None:
        if (
            "root_container" in data
            or "rootContainer" in data
            or "display_name" in data
        ):
            dashboards = [data]
        else:
            return [
                "YAML must contain 'dashboards' list or a single dashboard."
            ]

    if not isinstance(dashboards, list):
        return ["'dashboards' must be a list."]

    if not dashboards:
        return ["'dashboards' list is empty."]

    seen_ids: set[str] = set()
    for idx, dash in enumerate(dashboards):
        if not isinstance(dash, dict):
            errors.append(f"Dashboard at index {idx} must be a dictionary.")
            continue

        dash_id = (
            dash.get("dashboard_id")
            or dash.get("dashboardId")
            or (
                dash.get("name", "").split("/")[-1]
                if dash.get("name")
                else None
            )
            or dash.get("display_name")
            or dash.get("displayName")
            or f"dashboard_{idx}"
        )

        if str(dash_id) in seen_ids:
            errors.append(f"Duplicate dashboard_id found: '{dash_id}'")
        seen_ids.add(str(dash_id))

        display_name = dash.get("display_name") or dash.get("displayName")
        if not display_name:
            errors.append(
                f"Dashboard '{dash_id}' is missing required 'display_name'."
            )
        elif len(str(display_name)) > 100:
            errors.append(
                f"Dashboard '{dash_id}' display_name must not exceed 100 chars."
            )

        date_range = dash.get("date_range") or dash.get("dateRangeConfig")
        if date_range:
            _validate_date_range(
                date_range, f"Dashboard '{dash_id}'", errors
            )

        root_container = dash.get("root_container") or dash.get("rootContainer")
        if not root_container:
            errors.append(
                f"Dashboard '{dash_id}' is missing required 'root_container'."
            )
        else:
            _validate_container(
                root_container,
                f"Dashboard '{dash_id}' RootContainer",
                is_root=True,
                errors=errors,
            )

    return errors


def _normalize_date_range(dr: dict[str, Any]) -> dict[str, Any]:
    """Normalizes date range dictionary to API camelCase."""
    rel = dr.get("relative") or dr.get("relativeDateRange")
    abs_range = dr.get("absolute") or dr.get("absoluteDateRange")

    if rel and isinstance(rel, dict):
        unit = str(rel.get("unit", "DAY")).upper()
        return {
            "relativeDateRange": {
                "quantity": int(rel.get("quantity", 7)),
                "unit": unit,
            }
        }
    if abs_range and isinstance(abs_range, dict):
        return {"absoluteDateRange": abs_range}
    return dr


def _normalize_chart(chart_dict: dict[str, Any]) -> dict[str, Any]:
    """Normalizes chart dictionary to API camelCase."""
    vis_type = (
        chart_dict.get("chart_visualization_type")
        or chart_dict.get("chartVisualizationType")
        or chart_dict.get("visualization_type")
        or "CHART_VISUALIZATION_TYPE_UNSPECIFIED"
    )

    payload: dict[str, Any] = {
        "chartVisualizationType": str(vis_type).upper(),
    }

    disp = chart_dict.get("display_name") or chart_dict.get("displayName")
    if disp:
        payload["displayName"] = disp

    desc = chart_dict.get("description")
    if desc:
        payload["description"] = desc

    width = chart_dict.get("width")
    if width is not None:
        payload["width"] = int(width)

    height = chart_dict.get("height")
    if height is not None:
        payload["height"] = int(height)

    filt = chart_dict.get("filter")
    if filt:
        payload["filter"] = filt

    dr = chart_dict.get("date_range") or chart_dict.get("dateRangeConfig")
    if dr:
        payload["dateRangeConfig"] = _normalize_date_range(dr)

    ds = chart_dict.get("data_source") or chart_dict.get("dataSource")
    if ds and isinstance(ds, dict):
        gen = ds.get("generative_insights") or ds.get("generativeInsights")
        if gen and isinstance(gen, dict):
            sql = gen.get("sql_query") or gen.get("sqlQuery", "")
            raw_spec = gen.get("chart_spec") or gen.get("chartSpec", {})
            spec: dict[str, Any] = (
                dict(raw_spec) if isinstance(raw_spec, dict) else {}
            )

            # Ensure valid Vega-Lite specification
            if "$schema" not in spec:
                spec["$schema"] = (
                    "https://vega.github.io/schema/vega-lite/v5.json"
                )
            if "data" not in spec:
                spec["data"] = {"values": []}

            # Enhance default score card text styling if basic
            if str(vis_type).upper() == "SCORE_CARD":
                mark = spec.get("mark")
                if mark == "text":
                    spec["mark"] = {
                        "type": "text",
                        "fontSize": 28,
                        "fontWeight": 400,
                        "align": "center",
                        "baseline": "middle",
                        "font": "Google Sans",
                    }

            req_type = (
                "type.googleapis.com/google.cloud.contactcenterinsights.v1."
                "GenerativeInsightsRequest"
            )
            req = gen.get("request") or {"@type": req_type}
            payload["dataSource"] = {
                "generativeInsights": {
                    "sqlQuery": sql,
                    "chartSpec": spec,
                    "request": req,
                }
            }
        elif "query_metrics" in ds or "queryMetrics" in ds:
            qm = ds.get("query_metrics") or ds.get("queryMetrics")
            payload["dataSource"] = {"queryMetrics": qm}
        else:
            payload["dataSource"] = ds

    return payload


def _normalize_container(container_dict: dict[str, Any]) -> dict[str, Any]:
    """Recursively normalizes container dictionary to API camelCase."""
    payload: dict[str, Any] = {}

    disp = (
        container_dict.get("display_name")
        or container_dict.get("displayName")
    )
    if disp:
        payload["displayName"] = disp

    desc = container_dict.get("description")
    if desc:
        payload["description"] = desc

    width = container_dict.get("width")
    if width is not None:
        payload["width"] = int(width)

    height = container_dict.get("height")
    if height is not None:
        payload["height"] = int(height)

    filt = container_dict.get("filter")
    if filt:
        payload["filter"] = filt

    dr = (
        container_dict.get("date_range")
        or container_dict.get("dateRangeConfig")
    )
    if dr:
        payload["dateRangeConfig"] = _normalize_date_range(dr)

    raw_widgets = container_dict.get("widgets", [])
    normalized_widgets: list[dict[str, Any]] = []

    for w in raw_widgets:
        if not isinstance(w, dict):
            continue

        w_payload: dict[str, Any] = {}
        if "container" in w or "Container" in w:
            sub_c = w.get("container") or w.get("Container")
            w_payload["container"] = _normalize_container(sub_c)
        elif "chart" in w or "Chart" in w:
            chart = w.get("chart") or w.get("Chart")
            w_payload["chart"] = _normalize_chart(chart)
        elif "chart_reference" in w or "chartReference" in w:
            w_payload["chartReference"] = (
                w.get("chart_reference") or w.get("chartReference")
            )

        w_filt = w.get("filter")
        if w_filt:
            w_payload["filter"] = w_filt

        normalized_widgets.append(w_payload)

    payload["widgets"] = normalized_widgets
    return payload


def yaml_dashboard_to_api_payload(
    dashboard_dict: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Converts a declarative YAML dashboard dictionary to a CCAI API payload.

    Args:
        dashboard_dict: The dictionary representation of a dashboard from YAML.

    Returns:
        A tuple of (dashboard_id, api_payload_dict).
    """
    dash_id = (
        dashboard_dict.get("dashboard_id")
        or dashboard_dict.get("dashboardId")
        or (
            dashboard_dict.get("name", "").split("/")[-1]
            if dashboard_dict.get("name")
            else None
        )
        or dashboard_dict.get("display_name")
        or dashboard_dict.get("displayName")
        or "dashboard"
    )

    display_name = (
        dashboard_dict.get("display_name")
        or dashboard_dict.get("displayName")
        or str(dash_id)
    )

    payload: dict[str, Any] = {
        "displayName": display_name,
    }

    desc = dashboard_dict.get("description")
    if desc:
        payload["description"] = desc

    filt = dashboard_dict.get("filter")
    if filt:
        payload["filter"] = filt

    dr = (
        dashboard_dict.get("date_range")
        or dashboard_dict.get("dateRangeConfig")
    )
    if dr:
        payload["dateRangeConfig"] = _normalize_date_range(dr)

    root_c = (
        dashboard_dict.get("root_container")
        or dashboard_dict.get("rootContainer")
    )
    if root_c and isinstance(root_c, dict):
        payload["rootContainer"] = _normalize_container(root_c)

    return str(dash_id), payload


def api_dashboard_to_yaml_dashboard(
    api_dash: dict[str, Any]
) -> dict[str, Any]:
    """Converts an API response dictionary to a clean YAML dictionary.

    Args:
        api_dash: The dictionary response from the CCAI Insights API.

    Returns:
        A formatted YAML dashboard dictionary.
    """
    name = api_dash.get("name", "")
    dash_id = (
        name.split("/")[-1]
        if name
        else api_dash.get("displayName", "dashboard")
    )

    yaml_dash: dict[str, Any] = {
        "dashboard_id": dash_id,
        "display_name": api_dash.get("displayName", dash_id),
    }

    if "description" in api_dash:
        yaml_dash["description"] = api_dash["description"]

    if "filter" in api_dash:
        yaml_dash["filter"] = api_dash["filter"]

    if "dateRangeConfig" in api_dash:
        yaml_dash["date_range"] = api_dash["dateRangeConfig"]

    if "rootContainer" in api_dash:
        yaml_dash["root_container"] = api_dash["rootContainer"]

    return yaml_dash


def load_dashboards_yaml(file_path: str | Path) -> dict[str, Any]:
    """Loads and validates a dashboards YAML file.

    Args:
        file_path: Path to the YAML file.

    Returns:
        The loaded dictionary.

    Raises:
        ValueError: If file is invalid or fails schema validation.
        FileNotFoundError: If file does not exist.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Dashboards file not found: {file_path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"Invalid YAML content in {file_path}: Expected a mapping at root."
        )

    errors = validate_dashboards_dict(data)
    if errors:
        raise ValueError(
            f"Schema validation errors in {file_path}:\n - "
            + "\n - ".join(errors)
        )

    return data


def dump_dashboards_yaml(
    data: dict[str, Any], file_path: str | Path
) -> None:
    """Dumps a dashboards dictionary to a cleanly formatted YAML file.

    Args:
        data: The dictionary structure to serialize.
        file_path: Destination path for the YAML file.
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )


def export_remote_dashboards_to_yaml_dict(
    remote_dashboards: list[dict[str, Any]],
    project_id: str,
    location: str,
) -> dict[str, Any]:
    """Packages remote API dashboards into the standard declarative YAML format.

    Args:
        remote_dashboards: List of dashboards returned from Insights client.
        project_id: GCP project ID.
        location: GCP location.

    Returns:
        The declarative dictionary.
    """
    dashboards_yaml = [
        api_dashboard_to_yaml_dashboard(d)
        for d in remote_dashboards
        if not d.get("readOnly", False)
    ]
    return {
        "version": "1.0",
        "project_id": project_id,
        "location": location,
        "dashboards": dashboards_yaml,
    }


def _strip_container_server_fields(c: Any) -> Any:
    """Recursively removes server-assigned IDs and metadata for diffs."""
    if isinstance(c, dict):
        cleaned: dict[str, Any] = {}
        server_fields = (
            "containerId", "name", "createTime", "updateTime", "action"
        )
        for k, v in c.items():
            if k in server_fields:
                continue
            cleaned[k] = _strip_container_server_fields(v)
        return cleaned
    elif isinstance(c, list):
        return [_strip_container_server_fields(x) for x in c]
    return c


def diff_dashboards(
    local_data: dict[str, Any],
    remote_dashboards: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compares local YAML dashboards against active remote dashboards in GCP.

    Args:
        local_data: Parsed local YAML dictionary.
        remote_dashboards: List of active dashboards from CCAI Insights API.

    Returns:
        A diff dictionary with keys:
            - 'to_create': list of (dashboard_id, payload) to create
            - 'to_update': list of (dashboard_id, payload, mask, name) to update
            - 'to_delete': list of remote_name to delete
            - 'unchanged': list of dashboard_id that are already in sync
            - 'report': Human-readable formatted summary string
    """
    local_dashboards = local_data.get("dashboards")
    if local_dashboards is None:
        local_dashboards = [local_data]

    # Map remote dashboards by terminal ID
    remote_by_id: dict[str, dict[str, Any]] = {}
    for d in remote_dashboards:
        name = d.get("name", "")
        dash_id = name.split("/")[-1] if name else d.get("displayName", "")
        if dash_id:
            remote_by_id[dash_id] = d

    to_create: list[tuple[str, dict[str, Any]]] = []
    to_update: list[tuple[str, dict[str, Any], list[str], str]] = []
    unchanged: list[str] = []
    seen_remote_ids: set[str] = set()

    for d in local_dashboards:
        dash_id, payload = yaml_dashboard_to_api_payload(d)
        if dash_id not in remote_by_id:
            to_create.append((dash_id, payload))
        else:
            seen_remote_ids.add(dash_id)
            remote = remote_by_id[dash_id]
            remote_name = remote.get("name", "")
            changed_fields: list[str] = []

            if payload.get("displayName") != remote.get("displayName"):
                changed_fields.append("displayName")
            if (
                payload.get("description")
                and payload.get("description") != remote.get("description")
            ):
                changed_fields.append("description")

            norm_local_root = _strip_container_server_fields(
                payload.get("rootContainer")
            )
            norm_remote_root = _strip_container_server_fields(
                remote.get("rootContainer")
            )
            if norm_local_root != norm_remote_root:
                changed_fields.append("rootContainer")

            if changed_fields:
                to_update.append(
                    (dash_id, payload, changed_fields, remote_name)
                )
            else:
                unchanged.append(dash_id)

    # Remote custom dashboards missing from local file (excluding system)
    to_delete = [
        remote.get("name", "")
        for did, remote in remote_by_id.items()
        if did not in seen_remote_ids
        and remote.get("name")
        and not remote.get("readOnly", False)
    ]

    report = format_diff_report(
        to_create=to_create,
        to_update=to_update,
        to_delete=to_delete,
        unchanged=unchanged,
        remote_by_id=remote_by_id,
    )

    return {
        "to_create": to_create,
        "to_update": to_update,
        "to_delete": to_delete,
        "unchanged": unchanged,
        "report": report,
    }


def format_diff_report(
    to_create: list[tuple[str, dict[str, Any]]],
    to_update: list[tuple[str, dict[str, Any], list[str], str]],
    to_delete: list[str],
    unchanged: list[str],
    remote_by_id: dict[str, dict[str, Any]],
) -> str:
    """Formats a diff breakdown into a readable multi-line summary."""
    lines: list[str] = ["=== Configurable Dashboards Diff ==="]

    if not to_create and not to_update and not to_delete:
        lines.append(
            f"All {len(unchanged)} dashboard(s) are in sync with remote "
            "project."
        )
        return "\n".join(lines)

    if to_create:
        lines.append(f"\n[+] To Create ({len(to_create)}):")
        for did, payload in to_create:
            disp = payload.get("displayName")
            num_tabs = len(
                payload.get("rootContainer", {}).get("widgets", [])
            )
            lines.append(f"  + {did} (Title: '{disp}', Tabs: {num_tabs})")

    if to_update:
        lines.append(f"\n[~] To Update ({len(to_update)}):")
        for did, payload, fields, _ in to_update:
            lines.append(f"  ~ {did} (Fields changed: {', '.join(fields)})")
            remote = remote_by_id.get(did, {})
            if "rootContainer" in fields:
                local_json = json.dumps(
                    payload.get("rootContainer", {}), indent=2
                ).splitlines()
                remote_json = json.dumps(
                    remote.get("rootContainer", {}), indent=2
                ).splitlines()
                diff = list(
                    difflib.unified_diff(
                        remote_json,
                        local_json,
                        fromfile="remote",
                        tofile="local",
                        lineterm="",
                    )
                )
                for d in diff[:12]:
                    lines.append(f"      {d}")

    if to_delete:
        lines.append(f"\n[-] To Delete from remote ({len(to_delete)}):")
        for name in to_delete:
            lines.append(f"  - {name}")

    if unchanged:
        lines.append(
            f"\n[=] Unchanged ({len(unchanged)}): {', '.join(unchanged)}"
        )

    return "\n".join(lines)


def sync_dashboards(
    client: Insights,
    file_path: str | Path,
    parent: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Synchronizes local YAML dashboards to GCP Contact Center Insights.

    Args:
        client: The initialized Insights client.
        file_path: Path to the local dashboards.yaml file.
        parent: Optional parent override (e.g. projects/P/locations/L).
        force: If True, deletes remote dashboards missing from local YAML.
        dry_run: If True, calculates and prints diff without API calls.

    Returns:
        A dictionary summarizing executed actions:
            - 'created': list of created dashboard IDs
            - 'updated': list of updated dashboard IDs
            - 'deleted': list of deleted dashboard names
            - 'skipped_delete': list of remote dashboard names kept when
              force=False
    """
    local_data = load_dashboards_yaml(file_path)
    target_parent = parent or client.parent

    remote_dashboards = client.list_dashboards(parent=target_parent)
    diff = diff_dashboards(local_data, remote_dashboards)

    print(diff["report"])

    if dry_run:
        print("\n[Dry Run] No changes applied to GCP.")
        return {
            "created": [did for did, _ in diff["to_create"]],
            "updated": [did for did, _, _, _ in diff["to_update"]],
            "deleted": [],
            "skipped_delete": diff["to_delete"],
        }

    created: list[str] = []
    for dash_id, payload in diff["to_create"]:
        logger.info("Creating dashboard '%s'...", dash_id)
        client.create_dashboard(
            dashboard=payload,
            dashboard_id=dash_id,
            parent=target_parent,
        )
        created.append(dash_id)

    updated: list[str] = []
    for dash_id, payload, fields, remote_name in diff["to_update"]:
        logger.info("Updating dashboard '%s' (fields: %s)...", dash_id, fields)
        client.update_dashboard(
            name=remote_name,
            dashboard=payload,
            update_mask=fields,
        )
        updated.append(dash_id)

    deleted: list[str] = []
    skipped_delete: list[str] = []
    if diff["to_delete"]:
        if force:
            for remote_name in diff["to_delete"]:
                logger.info("Deleting remote dashboard '%s'...", remote_name)
                client.delete_dashboard(name=remote_name)
                deleted.append(remote_name)
        else:
            skipped_delete = diff["to_delete"]
            print(
                f"\n[Warning] {len(diff['to_delete'])} remote dashboard(s) "
                "not in local YAML were NOT deleted. Pass --force to delete."
            )

    return {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "skipped_delete": skipped_delete,
    }

