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

"""Tests for top-level lazy package imports and stripped-distribution guards."""

import json
import pathlib
import subprocess
import sys


# QA-008: Reproduction test — top-level `import cxas_scrapi` must remain lazy
# and must not eagerly import `cxas_scrapi.evals` (or `pytest`, `pandas`, etc.)
# as a side effect of computing `__all__`.
def test_qa_008_import_cxas_scrapi_preserves_lazy_subpackage_loading() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; import cxas_scrapi; "
                "print(json.dumps({"
                "'submodules': sorted(m for m in sys.modules "
                "if m.startswith('cxas_scrapi.')), "
                "'has_callback_evals': 'CallbackEvals' in cxas_scrapi.__all__"
                "}))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout.strip())
    assert payload["has_callback_evals"] is True
    assert payload["submodules"] == []


def test_qa_008_stripped_package_filters_missing_modules_from_all(
    tmp_path: pathlib.Path,
) -> None:
    import cxas_scrapi  # noqa: PLC0415

    init_src = pathlib.Path(cxas_scrapi.__file__).read_text(encoding="utf-8")
    pkg_dir = tmp_path / "cxas_scrapi"
    (pkg_dir / "core").mkdir(parents=True)
    (pkg_dir / "evals").mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text(init_src, encoding="utf-8")
    (pkg_dir / "core" / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "core" / "apps.py").write_text(
        "class Apps:\n    pass\n", encoding="utf-8"
    )
    # Synthesize an empty evals/__init__.py with NO callback_evals.py and NO
    # migration/ subpackage, mirroring stripped production distributions.
    (pkg_dir / "evals" / "__init__.py").write_text("", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
                "import cxas_scrapi; "
                "assert 'Apps' in cxas_scrapi.__all__; "
                "assert 'CallbackEvals' not in cxas_scrapi.__all__; "
                "assert 'DFCXAgents' not in cxas_scrapi.__all__; "
                "assert cxas_scrapi.Apps.__name__ == 'Apps'"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
