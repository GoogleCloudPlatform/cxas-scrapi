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

"""Pull/push YAML synchronization for CES Deployment resources.

CES ``ImportApp`` / ``ExportApp`` archives do not include ``Deployment``
resources (which are managed via the separate CES Deployments API). This module
exports live deployments to ``<app_root>/deployments/<deployment_id>.yaml`` and
syncs local deployment YAMLs back via ``Deployments.create_deployment`` /
``Deployments.update_deployment``, while safely ignoring ``cxas build``'s local
channel-metadata YAML files.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message as ProtoMessage

from cxas_scrapi.core.deployments import Deployments
from cxas_scrapi.utils.yaml_utils import Yaml12SafeLoader

if TYPE_CHECKING:
    from enum import Enum

_VOLATILE_DEPLOYMENT_KEYS = frozenset(
    {"name", "createTime", "updateTime", "etag"}
)
_DEPLOYMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_YAML_EXTS = frozenset({".yaml", ".yml"})


def _validate_deployment_id(raw_id: Any, *, context: str) -> str:
    """Validate a deployment identifier to prevent path traversal."""
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValueError(f"{context}: deploymentId must be a non-empty string.")
    if (
        raw_id != raw_id.strip()
        or ".." in raw_id
        or any(sep in raw_id for sep in ("/", "\\", "\x00", ":"))
        or not _DEPLOYMENT_ID_RE.match(raw_id)
    ):
        raise ValueError(
            f"{context}: invalid deploymentId {raw_id!r}; must match "
            f"{_DEPLOYMENT_ID_RE.pattern} without path separators or '..'."
        )
    return raw_id


def _assert_safe_dir_entry(path: Path, root: Path, *, label: str) -> None:
    """Reject symlinks and paths resolving outside ``root``."""
    resolved_root = root.resolve()
    curr = path
    checked: list[Path] = []
    while True:
        checked.append(curr)
        if curr in (root, resolved_root, curr.parent):
            break
        curr = curr.parent
    for node in checked:
        if node.is_symlink():
            raise ValueError(f"Symlinks are not permitted in {label}: {node}")
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"Path escapes {label} ({resolved_root}): {path}")


def _extract_id_from_name(name: str) -> str | None:
    """Extract `<deployment_id>` from `projects/.../deployments/<id>`."""
    marker = "/deployments/"
    if marker in name:
        return name.rsplit(marker, 1)[-1]
    return None


def _enum_to_str(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "name") and isinstance(value.name, str):
        return value.name
    if isinstance(value, str):
        return value
    return str(value)


def _deployment_to_raw_dict(deployment: Any) -> dict[str, Any]:
    """Convert a CES Deployment proto, mapping, or duck-typed object to dict."""
    if isinstance(deployment, Mapping):
        return dict(deployment)
    if hasattr(deployment, "_pb") and isinstance(deployment._pb, ProtoMessage):
        return MessageToDict(deployment._pb, preserving_proto_field_name=False)
    if isinstance(deployment, ProtoMessage):
        return MessageToDict(deployment, preserving_proto_field_name=False)

    out: dict[str, Any] = {}
    name = getattr(deployment, "name", None)
    if isinstance(name, str) and name:
        out["name"] = name
    dep_id = getattr(deployment, "deployment_id", None)
    if isinstance(dep_id, str) and dep_id:
        out["deploymentId"] = dep_id
    display_name = getattr(deployment, "display_name", None)
    if isinstance(display_name, str) and display_name:
        out["displayName"] = display_name
    app_version = getattr(deployment, "app_version", None)
    if isinstance(app_version, str) and app_version:
        out["appVersion"] = app_version

    cp = getattr(deployment, "channel_profile", None)
    if cp is not None:
        cp_dict: dict[str, Any] = {}
        if isinstance(cp, Mapping):
            cp_dict = dict(cp)
        else:
            ch_type = _enum_to_str(getattr(cp, "channel_type", None))
            if ch_type:
                cp_dict["channelType"] = ch_type
            if getattr(cp, "disable_dtmf", False):
                cp_dict["disableDtmf"] = True
            if getattr(cp, "disable_barge_in_control", False):
                cp_dict["disableBargeInControl"] = True
            nsl = getattr(cp, "noise_suppression_level", None)
            if isinstance(nsl, str) and nsl:
                cp_dict["noiseSuppressionLevel"] = nsl
            pp = getattr(cp, "persona_property", None)
            if pp is not None:
                persona = (
                    pp.get("persona")
                    if isinstance(pp, Mapping)
                    else _enum_to_str(getattr(pp, "persona", None))
                )
                if persona:
                    cp_dict["personaProperty"] = {"persona": persona}
            wwc = getattr(cp, "web_widget_config", None)
            if wwc is not None:
                wwc_dict: dict[str, Any] = {}
                if isinstance(wwc, Mapping):
                    wwc_dict = dict(wwc)
                else:
                    mod = _enum_to_str(getattr(wwc, "modality", None))
                    if mod:
                        wwc_dict["modality"] = mod
                    thm = _enum_to_str(getattr(wwc, "theme", None))
                    if thm:
                        wwc_dict["theme"] = thm
                    title = getattr(wwc, "web_widget_title", None)
                    if isinstance(title, str) and title:
                        wwc_dict["webWidgetTitle"] = title
                if wwc_dict:
                    cp_dict["webWidgetConfig"] = wwc_dict
        if cp_dict:
            out["channelProfile"] = cp_dict

    exp = getattr(deployment, "experiment_config", None)
    if exp is not None:
        if isinstance(exp, Mapping):
            if exp:
                out["experimentConfig"] = dict(exp)
        elif hasattr(exp, "_pb") and isinstance(exp._pb, ProtoMessage):
            exp_dict = MessageToDict(exp._pb, preserving_proto_field_name=False)
            if exp_dict:
                out["experimentConfig"] = exp_dict
        else:
            vr = getattr(exp, "version_release", None)
            if vr is not None:
                allocs_out: list[dict[str, Any]] = []
                for alloc in getattr(vr, "traffic_allocations", ()) or ():
                    a_ver = getattr(alloc, "app_version", None)
                    a_pct = getattr(alloc, "traffic_percentage", None)
                    if a_ver is not None and a_pct is not None:
                        allocs_out.append(
                            {
                                "appVersion": str(a_ver),
                                "trafficPercentage": int(a_pct),
                            }
                        )
                if allocs_out:
                    vr_dict: dict[str, Any] = {"trafficAllocations": allocs_out}
                    state = _enum_to_str(getattr(vr, "state", None))
                    if state:
                        vr_dict["state"] = state
                    out["experimentConfig"] = {"versionRelease": vr_dict}
    return out


def is_build_metadata_deployment(data: Mapping[str, Any]) -> bool:
    """Return True when ``data`` is a ``cxas build`` channel-metadata YAML."""
    return (
        ("profileId" in data or "brandId" in data)
        and "deploymentId" not in data
        and "appVersion" not in data
        and "experimentConfig" not in data
    )


def deployment_to_yaml_dict(deployment: Any) -> dict[str, Any]:
    """Convert a CES ``Deployment`` into a clean camelCase YAML dictionary.

    Extracts ``deploymentId`` from the resource ``name`` (or existing
    ``deploymentId`` field) and strips volatile server-managed envelope fields
    (``name``, ``createTime``, ``updateTime``, ``etag``).
    """
    raw = _deployment_to_raw_dict(deployment)
    raw_id = raw.get("deploymentId")
    if not raw_id and isinstance(raw.get("name"), str):
        raw_id = _extract_id_from_name(raw["name"])
    dep_id = _validate_deployment_id(raw_id, context="CES Deployment")

    cleaned: dict[str, Any] = {"deploymentId": dep_id}
    for key in (
        "displayName",
        "appVersion",
        "channelProfile",
        "experimentConfig",
    ):
        val = raw.get(key)
        if val is not None and val not in ("", {}):
            cleaned[key] = val

    for volatile in _VOLATILE_DEPLOYMENT_KEYS:
        cleaned.pop(volatile, None)
    return cleaned


def export_deployments_to_dir(
    deployments_client: Any, app_root: Path | str
) -> list[Path]:
    """Export live CES Deployments to ``<app_root>/deployments/<id>.yaml``.

    Preserves existing ``cxas build`` channel-metadata YAMLs without deleting
    or pruning them, validates every ``deploymentId``, and rejects symlinks
    under ``<app_root>/deployments``.
    """
    raw_root = Path(app_root)
    if raw_root.is_symlink():
        raise ValueError(f"Symlinks are not permitted as app_root: {raw_root}")
    root = raw_root.resolve()
    if not root.is_dir():
        raise ValueError(f"App root directory does not exist: {root}")

    dep_dir = root / "deployments"
    if dep_dir.is_symlink():
        raise ValueError(
            f"Symlinks are not permitted in deployments directory: {dep_dir}"
        )
    if dep_dir.exists():
        if not dep_dir.is_dir():
            raise ValueError(
                f"Expected deployments directory, found file: {dep_dir}"
            )
        _assert_safe_dir_entry(dep_dir, root, label="deployments directory")
        for existing in sorted(dep_dir.iterdir()):
            if existing.is_symlink():
                raise ValueError(
                    "Symlinks are not permitted in deployments directory: "
                    f"{existing}"
                )

    live_deployments = deployments_client.list_deployments()
    payloads: list[tuple[str, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for dep in live_deployments:
        doc = deployment_to_yaml_dict(dep)
        dep_id = doc["deploymentId"]
        if dep_id in seen_ids:
            raise ValueError(f"Duplicate deploymentId from CES API: {dep_id!r}")
        seen_ids.add(dep_id)
        payloads.append((dep_id, doc))

    payloads.sort(key=lambda item: item[0])
    if not payloads:
        return []

    dep_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for dep_id, doc in payloads:
        out_path = dep_dir / f"{dep_id}.yaml"
        _assert_safe_dir_entry(out_path, dep_dir, label="deployments directory")
        out_path.write_text(
            yaml.safe_dump(
                doc,
                sort_keys=True,
                allow_unicode=True,
                default_flow_style=False,
            ),
            encoding="utf-8",
        )
        written.append(out_path)
    return written


def _resolve_enum_member_name(
    enum_cls: type[Enum],
    raw_value: Any,
    *,
    field_label: str,
    path: Path,
) -> str:
    """Resolve an enum string by member name OR wire value (e.g. proto enum)."""
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(f"{path}: {field_label} must be a non-empty string.")
    upper = raw_value.strip().upper()
    member = enum_cls.__members__.get(upper)
    if member is not None:
        return member.name
    for cand in enum_cls:
        if isinstance(cand.value, str) and cand.value.upper() == upper:
            return cand.name
    valid = sorted(
        {m.name for m in enum_cls} | {str(m.value) for m in enum_cls}
    )
    raise ValueError(
        f"{path}: invalid {field_label} {raw_value!r}; expected one of {valid}."
    )


def _normalize_app_version(app_version: str, app_name: str | None) -> str:
    """Expand a bare version ID when ``app_name`` is set."""
    if app_version and not app_version.startswith("projects/") and app_name:
        return f"{app_name}/versions/{app_version}"
    return app_version


def _parse_deployment_yaml_file(
    path: Path, app_name: str | None
) -> tuple[str, dict[str, Any]] | None:
    """Parse one deployment YAML file; returns None for build-metadata YAMLs."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"Failed to read deployment file {path}: {exc}"
        ) from exc
    try:
        data = yaml.load(text, Loader=Yaml12SafeLoader)
    except yaml.YAMLError as exc:
        raise ValueError(
            f"Invalid YAML in deployment file {path}: {exc}"
        ) from exc

    if data is None:
        raise ValueError(f"Empty deployment YAML file: {path}")
    if not isinstance(data, dict):
        raise ValueError(
            f"Expected a YAML mapping at root of deployment file: {path}"
        )

    if is_build_metadata_deployment(data):
        return None

    raw_id = data.get("deploymentId", path.stem)
    dep_id = _validate_deployment_id(raw_id, context=str(path))

    fields: dict[str, Any] = {}

    if "displayName" in data and data["displayName"] is not None:
        dn = data["displayName"]
        if not isinstance(dn, str) or not dn.strip():
            raise ValueError(f"{path}: displayName must be a non-empty string.")
        fields["display_name"] = dn

    if "appVersion" in data and data["appVersion"] is not None:
        av = data["appVersion"]
        if not isinstance(av, str):
            raise ValueError(f"{path}: appVersion must be a string.")
        fields["app_version"] = _normalize_app_version(av, app_name)

    cp = data.get("channelProfile")
    if cp is not None:
        if not isinstance(cp, dict):
            raise ValueError(f"{path}: channelProfile must be a mapping.")
        if "channelType" in cp and cp["channelType"] is not None:
            fields["channel_type"] = _resolve_enum_member_name(
                Deployments.ChannelType,
                cp["channelType"],
                field_label="channelProfile.channelType",
                path=path,
            )
        if "disableDtmf" in cp and cp["disableDtmf"] is not None:
            if not isinstance(cp["disableDtmf"], bool):
                raise ValueError(
                    f"{path}: channelProfile.disableDtmf must be a boolean."
                )
            fields["disable_dtmf"] = cp["disableDtmf"]
        if (
            "disableBargeInControl" in cp
            and cp["disableBargeInControl"] is not None
        ):
            if not isinstance(cp["disableBargeInControl"], bool):
                raise ValueError(
                    f"{path}: channelProfile.disableBargeInControl must be a "
                    "boolean."
                )
            fields["disable_barge_in_control"] = cp["disableBargeInControl"]
        if (
            "noiseSuppressionLevel" in cp
            and cp["noiseSuppressionLevel"] is not None
        ):
            nsl = cp["noiseSuppressionLevel"]
            if not isinstance(nsl, str) or not nsl.strip():
                raise ValueError(
                    f"{path}: channelProfile.noiseSuppressionLevel must be a "
                    "non-empty string."
                )
            nsl_norm = nsl.strip().lower()
            if nsl_norm not in Deployments.VALID_NOISE_SUPPRESSION_LEVELS:
                raise ValueError(
                    f"{path}: invalid channelProfile.noiseSuppressionLevel "
                    f"{nsl!r}; expected one of "
                    f"{list(Deployments.VALID_NOISE_SUPPRESSION_LEVELS)}."
                )
            fields["noise_suppression_level"] = nsl_norm
        if "personaProperty" in cp and cp["personaProperty"] is not None:
            pp = cp["personaProperty"]
            if isinstance(pp, dict):
                persona_val = pp.get("persona")
                fields["persona_property"] = _resolve_enum_member_name(
                    Deployments.Persona,
                    persona_val,
                    field_label="channelProfile.personaProperty.persona",
                    path=path,
                )
            elif isinstance(pp, str) and pp.strip():
                fields["persona_property"] = _resolve_enum_member_name(
                    Deployments.Persona,
                    pp,
                    field_label="channelProfile.personaProperty",
                    path=path,
                )
            else:
                raise ValueError(
                    f"{path}: channelProfile.personaProperty must be a string "
                    "or mapping with 'persona'."
                )
        wwc = cp.get("webWidgetConfig")
        if wwc is not None:
            if not isinstance(wwc, dict):
                raise ValueError(
                    f"{path}: channelProfile.webWidgetConfig must be a mapping."
                )
            if "modality" in wwc and wwc["modality"] is not None:
                fields["modality"] = _resolve_enum_member_name(
                    Deployments.Modality,
                    wwc["modality"],
                    field_label="webWidgetConfig.modality",
                    path=path,
                )
            if "theme" in wwc and wwc["theme"] is not None:
                fields["theme"] = _resolve_enum_member_name(
                    Deployments.Theme,
                    wwc["theme"],
                    field_label="webWidgetConfig.theme",
                    path=path,
                )
            if "webWidgetTitle" in wwc and wwc["webWidgetTitle"] is not None:
                if not isinstance(wwc["webWidgetTitle"], str):
                    raise ValueError(
                        f"{path}: webWidgetConfig.webWidgetTitle must be a "
                        "string."
                    )
                fields["web_widget_title"] = wwc["webWidgetTitle"]

    exp = data.get("experimentConfig")
    if exp is not None:
        if not isinstance(exp, dict):
            raise ValueError(f"{path}: experimentConfig must be a mapping.")
        vr = exp.get("versionRelease")
        if vr is not None:
            if not isinstance(vr, dict):
                raise ValueError(
                    f"{path}: experimentConfig.versionRelease must be a "
                    "mapping."
                )
            allocs = vr.get("trafficAllocations")
            if allocs is not None:
                if not isinstance(allocs, list):
                    raise ValueError(
                        f"{path}: trafficAllocations must be a list."
                    )
                traffic_split: dict[str, int] = {}
                for item in allocs:
                    if not isinstance(item, dict):
                        raise ValueError(
                            f"{path}: each trafficAllocation must be a mapping."
                        )
                    a_ver = item.get("appVersion")
                    a_pct = item.get("trafficPercentage")
                    if not isinstance(a_ver, str) or not a_ver.strip():
                        raise ValueError(
                            f"{path}: trafficAllocation.appVersion must be a "
                            "non-empty string."
                        )
                    if (
                        isinstance(a_pct, bool)
                        or not isinstance(a_pct, int)
                        or not (0 <= a_pct <= 100)
                    ):
                        raise ValueError(
                            f"{path}: trafficAllocation.trafficPercentage must "
                            "be an integer in [0, 100]."
                        )
                    norm_ver = _normalize_app_version(a_ver, app_name)
                    if norm_ver in traffic_split:
                        raise ValueError(
                            f"{path}: duplicate appVersion {norm_ver!r} in "
                            "trafficAllocations."
                        )
                    traffic_split[norm_ver] = a_pct
                if traffic_split:
                    if len(traffic_split) < 2:
                        raise ValueError(
                            f"{path}: trafficAllocations requires at least two "
                            "distinct app versions."
                        )
                    if sum(traffic_split.values()) != 100:
                        raise ValueError(
                            f"{path}: trafficAllocations percentages must sum "
                            f"to 100 (got {sum(traffic_split.values())})."
                        )
                    fields["traffic_split"] = traffic_split

    return dep_id, fields


def sync_deployments_from_dir(
    deployments_client: Any, app_root: Path | str
) -> dict[str, list[str]]:
    """Sync ``<app_root>/deployments/*.{yaml,yml}`` via the CES Deployments API.

    Skips ``cxas build`` channel-metadata YAMLs cleanly (recording their
    filenames under ``"skipped"``), validates all deployment configs before
    issuing mutations, and calls ``update_deployment`` for existing IDs or
    ``create_deployment`` for new IDs.
    """
    raw_root = Path(app_root)
    if raw_root.is_symlink():
        raise ValueError(f"Symlinks are not permitted as app_root: {raw_root}")
    root = raw_root.resolve()
    if not root.is_dir():
        raise ValueError(f"App root directory does not exist: {root}")

    dep_dir = root / "deployments"
    if dep_dir.is_symlink():
        raise ValueError(
            f"Symlinks are not permitted in deployments directory: {dep_dir}"
        )
    if not dep_dir.exists():
        return {"created": [], "updated": [], "skipped": []}
    if not dep_dir.is_dir():
        raise ValueError(
            f"Expected deployments directory, found file: {dep_dir}"
        )
    _assert_safe_dir_entry(dep_dir, root, label="deployments directory")

    app_name = getattr(deployments_client, "app_name", None)
    skipped: list[str] = []
    specs: list[tuple[str, dict[str, Any], Path]] = []
    seen_ids: set[str] = set()

    for entry in sorted(dep_dir.iterdir()):
        if entry.is_symlink():
            raise ValueError(
                f"Symlinks are not permitted in deployments directory: {entry}"
            )
        if not entry.is_file() or entry.suffix.lower() not in _YAML_EXTS:
            continue
        _assert_safe_dir_entry(entry, dep_dir, label="deployments directory")
        parsed = _parse_deployment_yaml_file(entry, app_name)
        if parsed is None:
            skipped.append(entry.name)
            continue
        dep_id, fields = parsed
        if dep_id in seen_ids:
            raise ValueError(
                f"Duplicate deploymentId {dep_id!r} in deployments directory."
            )
        seen_ids.add(dep_id)
        specs.append((dep_id, fields, entry))

    created: list[str] = []
    updated: list[str] = []
    if not specs:
        return {"created": created, "updated": updated, "skipped": skipped}

    existing_ids: set[str] = set()
    for dep in deployments_client.list_deployments():
        doc = deployment_to_yaml_dict(dep)
        existing_ids.add(doc["deploymentId"])

    # Pre-validate required fields for all new deployments before mutating.
    for dep_id, fields, src_path in specs:
        if dep_id not in existing_ids:
            if not fields.get("display_name"):
                raise ValueError(
                    f"{src_path}: displayName is required to create new "
                    f"deployment {dep_id!r}."
                )
            if (
                fields.get("app_version") is None
                and "traffic_split" not in fields
            ):
                raise ValueError(
                    f"{src_path}: appVersion (or experimentConfig traffic "
                    f"split) is required to create new deployment {dep_id!r}."
                )

    for dep_id, fields, _src_path in specs:
        if dep_id in existing_ids:
            deployments_client.update_deployment(dep_id, **fields)
            updated.append(dep_id)
        else:
            create_kwargs: dict[str, Any] = {
                "deployment_id": dep_id,
                "display_name": fields["display_name"],
                "app_version": fields.get("app_version") or "",
            }
            for opt_key in (
                "channel_type",
                "modality",
                "theme",
                "web_widget_title",
                "disable_dtmf",
                "disable_barge_in_control",
                "persona_property",
                "noise_suppression_level",
                "traffic_split",
            ):
                if opt_key in fields:
                    create_kwargs[opt_key] = fields[opt_key]
            deployments_client.create_deployment(**create_kwargs)
            created.append(dep_id)

    return {"created": created, "updated": updated, "skipped": skipped}


__all__ = [
    "deployment_to_yaml_dict",
    "export_deployments_to_dir",
    "is_build_metadata_deployment",
    "sync_deployments_from_dir",
]
