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

from unittest.mock import Mock, patch

import pytest

from cxas_scrapi.utils.gcs_utils import GCSUtils


@patch("cxas_scrapi.utils.gcs_utils.storage.Client")
def test_upload_string_success(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_bucket = Mock()
    mock_blob = Mock()
    mock_client.get_bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob

    gcs = GCSUtils()
    gcs_uri = "gs://test-bucket/reports/report.html"
    content = "<html><body>Test</body></html>"

    res_url = gcs.upload_string(gcs_uri, content)

    mock_client.get_bucket.assert_called_once_with("test-bucket")
    mock_bucket.blob.assert_called_once_with("reports/report.html")
    mock_blob.upload_from_string.assert_called_once_with(
        content, content_type="text/html; charset=utf-8"
    )
    assert "test-bucket/reports/report.html" in res_url


@patch("cxas_scrapi.utils.gcs_utils.storage.Client")
def test_upload_string_invalid_scheme(mock_client_cls):
    gcs = GCSUtils()
    with pytest.raises(ValueError, match="Invalid GCS URI"):
        gcs.upload_string("https://storage.com/file", "content")


@patch("cxas_scrapi.utils.gcs_utils.storage.Client")
def test_upload_string_no_path(mock_client_cls):
    gcs = GCSUtils()
    with pytest.raises(ValueError, match="Invalid GCS URI"):
        gcs.upload_string("gs://bucket", "content")


@patch("cxas_scrapi.utils.gcs_utils.storage.Client")
def test_upload_file_calls_upload_from_filename(mock_client_cls, tmp_path):
    mock_client = mock_client_cls.return_value
    mock_bucket = Mock()
    mock_blob = Mock()
    mock_client.get_bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob

    gcs = GCSUtils()
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc")
    url = gcs.upload_file(
        "gs://bucket/path/x.bin", str(f), content_type="application/octet"
    )
    mock_blob.upload_from_filename.assert_called_once_with(
        str(f), content_type="application/octet"
    )
    assert "bucket/path/x.bin" in url


@patch("cxas_scrapi.utils.gcs_utils.storage.Client")
def test_download_blob_returns_bytes(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_bucket = Mock()
    mock_blob = Mock()
    mock_client.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob
    mock_blob.download_as_bytes.return_value = b"hello"

    gcs = GCSUtils()
    assert gcs.download_blob("gs://b/p") == b"hello"


@patch("cxas_scrapi.utils.gcs_utils.storage.Client")
def test_download_string_decodes(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_bucket = Mock()
    mock_blob = Mock()
    mock_client.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob
    mock_blob.download_as_bytes.return_value = "héllo".encode("utf-8")

    gcs = GCSUtils()
    assert gcs.download_string("gs://b/p") == "héllo"


@patch("cxas_scrapi.utils.gcs_utils.storage.Client")
def test_download_to_file_creates_dirs(mock_client_cls, tmp_path):
    mock_client = mock_client_cls.return_value
    mock_bucket = Mock()
    mock_blob = Mock()
    mock_client.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob

    gcs = GCSUtils()
    dest = tmp_path / "nested" / "dir" / "audio.wav"
    out = gcs.download_to_file("gs://b/audio.wav", str(dest))
    mock_blob.download_to_filename.assert_called_once_with(str(dest))
    assert out == str(dest.absolute())


@patch("cxas_scrapi.utils.gcs_utils.storage.Client")
def test_exists_returns_bool(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_bucket = Mock()
    mock_blob = Mock()
    mock_client.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob
    mock_blob.exists.return_value = True

    gcs = GCSUtils()
    assert gcs.exists("gs://b/p") is True
    mock_blob.exists.assert_called_once_with(mock_client)


@patch("cxas_scrapi.utils.gcs_utils.storage.Client")
def test_parse_gcs_uri_validation_paths(mock_client_cls):
    gcs = GCSUtils()
    with pytest.raises(ValueError):
        gcs._parse_gcs_uri("not-a-gs-uri")
    with pytest.raises(ValueError):
        gcs._parse_gcs_uri("gs://bucket/")


@patch("cxas_scrapi.utils.gcs_utils.storage.Client")
def test_find_first_returns_match(mock_client_cls):
    mock_client = mock_client_cls.return_value
    blob_a = Mock(name="a")
    blob_a.name = "x/y/conv-1/full-session.wav"
    blob_b = Mock(name="b")
    blob_b.name = "x/y/conv-1/agent-turn-1.wav"
    mock_client.list_blobs.return_value = [blob_b, blob_a]

    gcs = GCSUtils()
    out = gcs.find_first(
        "gs://my-bucket", suffix="/conv-1/full-session.wav"
    )
    assert out == "gs://my-bucket/x/y/conv-1/full-session.wav"


@patch("cxas_scrapi.utils.gcs_utils.storage.Client")
def test_find_first_returns_none(mock_client_cls):
    mock_client = mock_client_cls.return_value
    blob_a = Mock(name="a")
    blob_a.name = "other/file.wav"
    mock_client.list_blobs.return_value = [blob_a]

    gcs = GCSUtils()
    assert gcs.find_first("my-bucket", suffix="/missing.wav") is None
