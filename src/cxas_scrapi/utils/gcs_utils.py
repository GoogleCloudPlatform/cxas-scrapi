"""Utility class for interacting with Google Cloud Storage."""

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

import os
from typing import Any

from google.cloud import storage

from cxas_scrapi.core.common import Common


class GCSUtils(Common):
    """Utility class for Google Cloud Storage integrations."""

    def __init__(
        self,
        creds_path: str | None = None,
        creds_dict: dict[str, str] | None = None,
        creds: Any = None,
        scope: list[str] | None = None,
    ):
        """Initializes GCSUtils with common auth logic.

        Args:
            creds_path: Path to service account JSON file.
            creds_dict: Service account credentials as a dictionary.
            creds: Service account credentials object.
            scope: List of scopes for the credentials.
        """
        super().__init__(
            creds_path=creds_path,
            creds_dict=creds_dict,
            creds=creds,
            scope=scope,
        )
        self.client = storage.Client(
            credentials=self.creds,
            project=self.project_id,
            client_info=self.client_info,
        )

    def upload_string(
        self,
        gcs_uri: str,
        content: str,
        content_type: str = "text/html; charset=utf-8",
    ) -> str:
        """Uploads a string to a GCS URI and returns the mtls URL.

        Args:
            gcs_uri: The full GCS URI (e.g., gs://bucket/path/to/file).
            content: The string content to upload.
            content_type: The MIME type of the content.

        Returns:
            The authenticated URL for the uploaded file.

        Raises:
            ValueError: If the GCS URI is invalid.
        """
        bucket_name, blob_path = self._parse_gcs_uri(gcs_uri)
        bucket = self.client.get_bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.upload_from_string(content, content_type=content_type)

        return (
            f"https://storage.mtls.cloud.google.com/{bucket_name}/{blob_path}"
        )

    def upload_file(
        self,
        gcs_uri: str,
        local_path: str,
        content_type: str | None = None,
    ) -> str:
        """Uploads a local file to a GCS URI and returns the mtls URL."""
        bucket_name, blob_path = self._parse_gcs_uri(gcs_uri)
        bucket = self.client.get_bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.upload_from_filename(local_path, content_type=content_type)
        return (
            f"https://storage.mtls.cloud.google.com/{bucket_name}/{blob_path}"
        )

    def download_blob(self, gcs_uri: str) -> bytes:
        """Downloads a blob from GCS and returns its raw bytes."""
        bucket_name, blob_path = self._parse_gcs_uri(gcs_uri)
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        return blob.download_as_bytes()

    def download_string(self, gcs_uri: str, encoding: str = "utf-8") -> str:
        """Downloads a blob from GCS and decodes it as text."""
        return self.download_blob(gcs_uri).decode(encoding)

    def download_to_file(self, gcs_uri: str, dest_path: str) -> str:
        """Downloads a blob from GCS to a local file path.

        Creates parent directories as needed. Returns the absolute dest path.
        """
        bucket_name, blob_path = self._parse_gcs_uri(gcs_uri)
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        blob.download_to_filename(dest_path)
        return os.path.abspath(dest_path)

    def exists(self, gcs_uri: str) -> bool:
        """Returns True if the GCS object exists."""
        bucket_name, blob_path = self._parse_gcs_uri(gcs_uri)
        bucket = self.client.bucket(bucket_name)
        return bucket.blob(blob_path).exists(self.client)

    def find_first(
        self,
        gcs_bucket_uri: str,
        suffix: str,
        max_results: int = 5000,
    ) -> str | None:
        """Lists the bucket and returns the first object whose name ends with
        `suffix` (a fragment like `{conversation_id}/full-session.wav`).
        Returns the full `gs://...` URI or None.
        """
        if gcs_bucket_uri.startswith("gs://"):
            bucket_name = gcs_bucket_uri[5:].split("/", 1)[0]
        else:
            bucket_name = gcs_bucket_uri
        for blob in self.client.list_blobs(
            bucket_name, max_results=max_results
        ):
            if blob.name.endswith(suffix):
                return f"gs://{bucket_name}/{blob.name}"
        return None

    @staticmethod
    def _parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
        """Splits a gs:// URI into (bucket, blob_path)."""
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")
        parts = gcs_uri[5:].split("/", 1)
        if len(parts) < 2 or not parts[1]:
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")
        return parts[0], parts[1]
