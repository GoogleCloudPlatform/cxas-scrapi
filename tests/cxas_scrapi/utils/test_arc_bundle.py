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

"""Tests for the Arc attachment bundle builder."""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cxas_scrapi.utils import arc_bundle


def _write(root: Path, rel_path: str, content: str) -> str:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def _write_zip(root: Path, rel_path: str, entries: dict[str, str]) -> str:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return str(path)


def _zip_bytes(bundle: arc_bundle.Bundle) -> bytes:
    assert bundle.zip_bytes is not None
    return bundle.zip_bytes


def _zip_names(bundle: arc_bundle.Bundle) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(_zip_bytes(bundle))) as zf:
        return sorted(zf.namelist())


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Creates <tmp>/team/{app,evals} with a few files and noise entries."""
    _write(tmp_path, "team/app/app.json", '{"displayName": "a"}')
    _write(tmp_path, "team/app/agents/root/instruction.txt", "be nice")
    _write(tmp_path, "team/evals/sim_evals/simulations.yaml", "evals: []\n")
    _write(tmp_path, "team/evals/audio.wav", "RIFF")
    _write(tmp_path, "team/evals/__pycache__/x.pyc", "x")
    _write(tmp_path, "team/evals/bazel-out/junk.txt", "junk")
    _write(tmp_path, "team/evals/bazel-bin/junk.txt", "junk")
    return tmp_path


@pytest.fixture
def team(workspace: Path) -> str:
    return str(workspace / "team")


def test_build_keeps_relative_paths_and_excludes_noise(
    workspace: Path, team: str
) -> None:
    bundle = arc_bundle.build(
        ["team/app", "team/evals"], working_dir=str(workspace)
    )
    assert bundle.root == team
    assert bundle.inputs == ["app", "evals"]
    assert _zip_names(bundle) == [
        "app/agents/root/instruction.txt",
        "app/app.json",
        arc_bundle.MANIFEST_NAME,
        "evals/sim_evals/simulations.yaml",
    ]
    assert bundle.excluded_count == 4


def test_excludes_vcs_dirs_and_symlinks(workspace: Path, team: str) -> None:
    _write(workspace, "team/.git/config", "[core]")
    _write(workspace, "team/.hg/hgrc", "")
    _write(workspace, "team/node_modules/pkg/index.js", "x")
    os.symlink(
        os.path.join(team, "app", "app.json"), os.path.join(team, "link.json")
    )
    bundle = arc_bundle.build([team])
    names = _zip_names(bundle)
    assert not any(
        n.startswith((".git/", ".hg/", "node_modules/")) for n in names
    )
    assert "link.json" not in names
    assert "app/app.json" in names


def test_manifest_records_provenance(workspace: Path, team: str) -> None:
    app = os.path.join(team, "app")
    bundle = arc_bundle.build([app], working_dir=str(workspace))
    with zipfile.ZipFile(io.BytesIO(_zip_bytes(bundle))) as zf:
        manifest = json.loads(zf.read(arc_bundle.MANIFEST_NAME))
    assert manifest["version"] == arc_bundle.BUNDLE_VERSION
    assert manifest["source"] == {"type": "local", "root": "app"}
    assert manifest["inputs"] == ["."]
    assert manifest["file_count"] == 2
    assert manifest["uncompressed_bytes"] == bundle.uncompressed_bytes
    assert "created_at" in manifest


def test_relative_path_and_single_file(team: str) -> None:
    bundle = arc_bundle.build(
        ["evals/sim_evals/simulations.yaml"], working_dir=team
    )
    assert bundle.inputs == ["simulations.yaml"]
    assert len(bundle.files) == 1


def test_missing_path_raises(workspace: Path) -> None:
    with pytest.raises(arc_bundle.BundleError, match="does not exist"):
        arc_bundle.build(["nope"], working_dir=str(workspace))


def test_no_paths_raises() -> None:
    with pytest.raises(arc_bundle.BundleError, match="No attach paths"):
        arc_bundle.build([])


def test_file_count_limit_reports_largest_dirs(team: str) -> None:
    limits = arc_bundle.BundleLimits(max_files=1)
    with pytest.raises(
        arc_bundle.BundleError, match=r"(?s)3 files \(limit 1\).*Largest"
    ):
        arc_bundle.build([team], limits=limits)


def test_uncompressed_limit(workspace: Path, team: str) -> None:
    _write(workspace, "team/evals/big.json", "x" * 5000)
    limits = arc_bundle.BundleLimits(max_uncompressed_bytes=4096)
    with pytest.raises(
        arc_bundle.BundleError, match=r"(?s)uncompressed.*evals/ = "
    ):
        arc_bundle.build([team], limits=limits)


def test_limit_breakdown_is_relative_to_inputs(
    workspace: Path, team: str
) -> None:
    _write(workspace, "team/app/tools/big.json", "x" * 5000)
    limits = arc_bundle.BundleLimits(max_uncompressed_bytes=4096)
    with pytest.raises(
        arc_bundle.BundleError,
        match=r"(?s)Largest entries:\n  app/tools/ = ",
    ):
        arc_bundle.build(
            [os.path.join(team, "app"), os.path.join(team, "evals")],
            limits=limits,
        )


def test_zip_limit(workspace: Path, team: str) -> None:
    _write(workspace, "team/evals/random.json", os.urandom(4096).hex())
    limits = arc_bundle.BundleLimits(max_zip_bytes=1024)
    with pytest.raises(arc_bundle.BundleError, match="Bundle zip is"):
        arc_bundle.build([team], limits=limits)


def test_zip_limit_cannot_exceed_server_limit(team: str) -> None:
    limits = arc_bundle.BundleLimits(
        max_zip_bytes=arc_bundle.SERVER_MAX_ZIP_BYTES + 1
    )
    with pytest.raises(arc_bundle.BundleError, match="server limit"):
        arc_bundle.build([team], limits=limits)


def test_only_excluded_files_raises(team: str) -> None:
    with pytest.raises(arc_bundle.BundleError, match="No files"):
        arc_bundle.build([os.path.join(team, "evals", "audio.wav")])


def test_single_zip_is_attached_unchanged(workspace: Path) -> None:
    path = _write_zip(
        workspace,
        "exports/My App.zip",
        {"my_app/app.json": "{}", "my_app/evals/sim.yaml": "evals: []\n"},
    )
    bundle = arc_bundle.build(
        ["exports/My App.zip"], working_dir=str(workspace)
    )
    assert bundle.zip_bytes == Path(path).read_bytes()
    assert [f.rel_path for f in bundle.files] == [
        "my_app/app.json",
        "my_app/evals/sim.yaml",
    ]
    assert bundle.inputs == ["My App.zip"]
    assert bundle.source_zip == path
    assert arc_bundle.bundle_file_name(bundle) == "My_App.zip"


def test_zip_mixed_with_other_paths_raises(workspace: Path, team: str) -> None:
    _write_zip(workspace, "exports/app.zip", {"app.json": "{}"})
    with pytest.raises(arc_bundle.BundleError, match="on its own"):
        arc_bundle.build(
            ["exports/app.zip", os.path.join(team, "app")],
            working_dir=str(workspace),
        )


def test_invalid_zip_raises(workspace: Path) -> None:
    _write(workspace, "exports/broken.zip", "not a zip")
    with pytest.raises(arc_bundle.BundleError, match="Not a valid zip"):
        arc_bundle.build(["exports/broken.zip"], working_dir=str(workspace))


def test_zip_limits_apply_to_attached_zip(workspace: Path) -> None:
    path = _write_zip(
        workspace, "exports/big.zip", {"a.txt": os.urandom(4096).hex()}
    )
    with pytest.raises(arc_bundle.BundleError, match="Zip is"):
        arc_bundle.build(
            [path], limits=arc_bundle.BundleLimits(max_zip_bytes=1024)
        )
    with pytest.raises(arc_bundle.BundleError, match="uncompressed"):
        arc_bundle.build(
            [path],
            limits=arc_bundle.BundleLimits(max_uncompressed_bytes=1024),
        )


def test_empty_zip_raises(workspace: Path) -> None:
    path = _write_zip(workspace, "exports/empty.zip", {})
    with pytest.raises(arc_bundle.BundleError, match="No files"):
        arc_bundle.build([path])


def test_zip_inside_directory_is_still_excluded(
    workspace: Path, team: str
) -> None:
    _write_zip(workspace, "team/evals/nested.zip", {"x.txt": "x"})
    bundle = arc_bundle.build([team])
    assert "evals/nested.zip" not in _zip_names(bundle)


def test_format_report(team: str) -> None:
    bundle = arc_bundle.build([team])
    report = arc_bundle.format_report(
        bundle, max_listed=1, uploaded_uri="gs://b/o.zip"
    )
    assert "### [Attachment Bundle]" in report
    assert f"`{team}`" in report
    assert "- **Files**: 3 (excluded: 4)" in report
    assert "- **Zip**: " in report
    assert "`gs://b/o.zip`" in report
    assert "... and 2 more" in report


def test_format_report_without_zip_or_upload(team: str) -> None:
    bundle = arc_bundle.collect([team])
    report = arc_bundle.format_report(bundle)
    assert "**Zip**" not in report
    assert "**Uploaded**" not in report
    assert "app/app.json" in report


def test_bundle_file_name(team: str) -> None:
    bundle = arc_bundle.build([team])
    assert arc_bundle.bundle_file_name(bundle) == "arc_bundle_team.zip"


def test_default_artifacts_bucket() -> None:
    assert (
        arc_bundle.default_artifacts_bucket("my-proj")
        == "my-proj-agent-assist-artifacts"
    )


def test_upload_bundle_uses_storage_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "arc_bundle_team.zip"
    local.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    client_cls = MagicMock()
    monkeypatch.setattr("google.cloud.storage.Client", client_cls)
    creds = object()

    uri = arc_bundle.upload_bundle(
        str(local), "my-bucket", credentials=creds, project="my-proj"
    )

    assert re.fullmatch(
        r"gs://my-bucket/agent-assist-uploads/\d+_arc_bundle_team\.zip", uri
    )
    client_cls.assert_called_once_with(credentials=creds, project="my-proj")
    client = client_cls.return_value
    client.bucket.assert_called_once_with("my-bucket")
    blob_name = client.bucket.return_value.blob.call_args.args[0]
    assert uri == f"gs://my-bucket/{blob_name}"
    blob = client.bucket.return_value.blob.return_value
    blob.upload_from_filename.assert_called_once_with(
        str(local), content_type="application/zip"
    )


def test_upload_bundle_custom_prefix_and_gs_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "x.zip"
    local.write_bytes(b"")
    client_cls = MagicMock()
    monkeypatch.setattr("google.cloud.storage.Client", client_cls)

    uri = arc_bundle.upload_bundle(
        str(local), "gs://b/", credentials=None, prefix="/custom/"
    )

    assert re.fullmatch(r"gs://b/custom/\d+_x\.zip", uri)
    client_cls.return_value.bucket.assert_called_once_with("b")


def test_download_gcs_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_cls = MagicMock()
    monkeypatch.setattr("google.cloud.storage.Client", client_cls)
    dest = tmp_path / "out" / "nested"
    creds = object()

    path = arc_bundle.download_gcs_uri(
        "gs://b/dir/report.md", str(dest), credentials=creds
    )

    assert path == str(dest / "report.md")
    assert dest.is_dir()
    client_cls.assert_called_once_with(credentials=creds)
    client = client_cls.return_value
    client.bucket.assert_called_once_with("b")
    client.bucket.return_value.blob.assert_called_once_with("dir/report.md")
    blob = client.bucket.return_value.blob.return_value
    blob.download_to_filename.assert_called_once_with(path)


@pytest.mark.parametrize("uri", ["/tmp/x", "gs://", "gs://bucket-only"])
def test_download_rejects_bad_uri(uri: str, tmp_path: Path) -> None:
    with pytest.raises(arc_bundle.BundleError, match="gs://"):
        arc_bundle.download_gcs_uri(uri, str(tmp_path), credentials=None)


def test_resolve_project_id_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_cls = MagicMock()
    monkeypatch.setattr(
        "google.auth.transport.requests.AuthorizedSession", session_cls
    )
    assert arc_bundle.resolve_project_id("my-proj", credentials=None) == (
        "my-proj"
    )
    session_cls.assert_not_called()


def test_resolve_project_id_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    session_cls = MagicMock()
    response = session_cls.return_value.get.return_value
    response.json.return_value = {"projectId": "my-proj"}
    monkeypatch.setattr(
        "google.auth.transport.requests.AuthorizedSession", session_cls
    )
    creds = object()

    assert arc_bundle.resolve_project_id("123456", credentials=creds) == (
        "my-proj"
    )

    session_cls.assert_called_once_with(creds)
    url = session_cls.return_value.get.call_args.args[0]
    assert url == (
        "https://cloudresourcemanager.googleapis.com/v3/projects/123456"
    )
    response.raise_for_status.assert_called_once_with()


def test_resolve_project_id_failure_hints_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_cls = MagicMock()
    session_cls.return_value.get.side_effect = OSError("boom")
    monkeypatch.setattr(
        "google.auth.transport.requests.AuthorizedSession", session_cls
    )
    with pytest.raises(RuntimeError, match=r"(?s)boom.*--bucket"):
        arc_bundle.resolve_project_id("123456", credentials=None)


def test_resolve_project_id_missing_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_cls = MagicMock()
    session_cls.return_value.get.return_value.json.return_value = {}
    monkeypatch.setattr(
        "google.auth.transport.requests.AuthorizedSession", session_cls
    )
    with pytest.raises(RuntimeError, match="--bucket"):
        arc_bundle.resolve_project_id("123456", credentials=None)


def test_excludes_secrets_and_env_dirs_by_default(
    workspace: Path, team: str
) -> None:
    _write(workspace, "team/.env", "API_KEY=secret")
    _write(workspace, "team/.env.production", "TOKEN=secret")
    _write(workspace, "team/tls.pem", "-----BEGIN PRIVATE KEY-----")
    _write(workspace, "team/private.key", "secret")
    _write(workspace, "team/service_account_prod.json", "{}")
    _write(workspace, "team/client_credentials.json", "{}")
    _write(workspace, "team/.ssh/id_rsa", "secret")
    _write(workspace, "team/.venv/pyvenv.cfg", "home = /usr")
    _write(workspace, "team/.terraform/state.tfstate", "{}")

    bundle = arc_bundle.build([team])
    names = _zip_names(bundle)
    assert names == [
        "app/agents/root/instruction.txt",
        "app/app.json",
        arc_bundle.MANIFEST_NAME,
        "evals/sim_evals/simulations.yaml",
    ]
    assert bundle.secret_excluded_count == 6
    report = arc_bundle.format_report(bundle)
    assert "- **Secrets skipped**: 6" in report

    allowed = arc_bundle.build([team], allow_secrets=True)
    allowed_names = _zip_names(allowed)
    assert ".env" in allowed_names
    assert "service_account_prod.json" in allowed_names
    assert allowed.secret_excluded_count == 0
    # Sensitive directories (.ssh, .venv, .terraform) remain excluded.
    assert not any(
        n.startswith((".ssh/", ".venv/", ".terraform/")) for n in allowed_names
    )


@pytest.mark.parametrize(
    "unsafe_member",
    [
        "../evil.txt",
        "a/../../evil.txt",
        "/etc/passwd",
        r"sub\evil.txt",
        "C:x.txt",
    ],
)
def test_prebuilt_zip_rejects_unsafe_member_paths(
    workspace: Path, unsafe_member: str
) -> None:
    path = _write_zip(workspace, "exports/unsafe.zip", {unsafe_member: "x"})
    with pytest.raises(arc_bundle.BundleError, match="Unsafe path"):
        arc_bundle.build([path])


def test_upload_bundle_gs_uri_with_subprefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "x.zip"
    local.write_bytes(b"PK")
    client_cls = MagicMock()
    monkeypatch.setattr("google.cloud.storage.Client", client_cls)

    uri = arc_bundle.upload_bundle(
        str(local), "gs://my-bucket/team-sub/uploads", credentials=None
    )

    assert re.fullmatch(
        r"gs://my-bucket/team-sub/uploads/agent-assist-uploads/\d+_x\.zip", uri
    )
    client_cls.return_value.bucket.assert_called_once_with("my-bucket")


@pytest.mark.parametrize("uri", ["gs://b/dir/", "gs://b/.", "gs://b/.."])
def test_download_rejects_unsafe_basename(uri: str, tmp_path: Path) -> None:
    with pytest.raises(arc_bundle.BundleError, match="Unsafe object name"):
        arc_bundle.download_gcs_uri(uri, str(tmp_path), credentials=None)


def test_download_gcs_uri_forwards_project_and_avoids_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_cls = MagicMock()

    def write_dummy(dest_file: str) -> None:
        Path(dest_file).write_text("downloaded", encoding="utf-8")

    client_cls.return_value.bucket.return_value.blob.return_value.download_to_filename.side_effect = write_dummy
    monkeypatch.setattr("google.cloud.storage.Client", client_cls)
    creds = object()

    first = arc_bundle.download_gcs_uri(
        "gs://b/a/report.md",
        str(tmp_path),
        credentials=creds,
        project="proj-1",
    )
    second = arc_bundle.download_gcs_uri(
        "gs://b/b/report.md",
        str(tmp_path),
        credentials=creds,
        project="proj-1",
    )

    assert first == str(tmp_path / "report.md")
    assert second == str(tmp_path / "report-1.md")
    client_cls.assert_called_with(credentials=creds, project="proj-1")
