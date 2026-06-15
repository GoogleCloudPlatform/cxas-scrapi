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
from unittest.mock import patch
from cxas_scrapi.reporting import base_components
from cxas_scrapi.reporting.base_components import (
    _TEMPLATE_PATH_BY_NAME,
    build_template_registry,
)
import pytest


def test_build_template_registry_success():
    # Mock os.walk to return a clean registry structure with no duplicates
    with (
        patch("os.walk") as mock_walk,
        patch("os.path.relpath") as mock_relpath,
    ):
        mock_walk.return_value = [
            ("/mock/components", [], ["template_a.html", "template_b.html"]),
            ("/mock/components/sub", [], ["template_c.html"]),
        ]

        # We mock relpath to map files cleanly
        def mock_relpath_side_effect(path, start):
            return os.path.basename(path)  # Just return basename for simplicity

        mock_relpath.side_effect = mock_relpath_side_effect

        build_template_registry()

        assert _TEMPLATE_PATH_BY_NAME == {
            "template_a": "template_a.html",
            "template_b": "template_b.html",
            "template_c": "template_c.html",
        }


def test_build_template_registry_duplicate_raises_error():
    # Mock os.walk to return a duplicate template name in different folders
    with (
        patch("os.walk") as mock_walk,
        patch("os.path.relpath") as mock_relpath,
    ):
        mock_walk.return_value = [
            ("/mock/components", [], ["template_a.html"]),
            ("/mock/components/sub", [], ["template_a.html"]),  # Duplicate!
        ]

        # Let's map different relative paths for them
        paths = {
            "/mock/components/template_a.html": "template_a.html",
            "/mock/components/sub/template_a.html": "sub/template_a.html",
        }

        def mock_relpath_side_effect(path, start):
            return paths.get(path, path)

        mock_relpath.side_effect = mock_relpath_side_effect

        with pytest.raises(ValueError) as exc_info:
            build_template_registry()

        assert "Duplicate template name detected in directory" in str(
            exc_info.value
        )
        assert "template_a" in str(exc_info.value)


def test_component_resolution_failure_raises_file_not_found():
    class UnregisteredComponent(base_components.Component):
        def render(self):
            return self.substitute()

    with pytest.raises(FileNotFoundError):
        UnregisteredComponent().get_resolved_template()


def test_custom_template_dir_registration(tmp_path):
    # Create a custom empty_component.html in the temp directory
    custom_template_file = tmp_path / "empty_component.html"
    custom_template_file.write_text("<!-- custom empty -->", encoding="utf-8")

    try:
        # Register the temp directory
        base_components.register_template_dir(tmp_path)

        # Render EmptyComponent and assert it uses the custom template
        rendered = base_components.EmptyComponent().render()
        assert rendered == "<!-- custom empty -->"
    finally:
        # Clean up: remove the temp path from base_components._TEMPLATE_DIRS
        if tmp_path in base_components._TEMPLATE_DIRS:
            base_components._TEMPLATE_DIRS.remove(tmp_path)
        base_components.load_component.cache_clear()
        base_components._read_file.cache_clear()
        base_components.build_template_registry()
