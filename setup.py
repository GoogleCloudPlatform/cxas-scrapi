"""Setuptools for CXAS SCRAPI package."""

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
import pathlib

from setuptools import find_packages, setup

here = pathlib.Path(__file__).parent.resolve()

# Get the long description from the README file
long_description = (here / "README.md").read_text(encoding="utf-8")


def _collect_data_files(source_dirs, root_files):
    """Walk directories and collect (dest, [files]) for data_files."""
    result = []
    for src_dir in source_dirs:
        if not os.path.isdir(src_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(src_dir):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            files = [
                os.path.join(dirpath, f)
                for f in filenames
                if not f.endswith(".pyc")
            ]
            if files:
                dest = os.path.join("share/cxas-scrapi/skills", dirpath)
                result.append((dest, files))
    if root_files:
        existing = [f for f in root_files if os.path.exists(f)]
        if existing:
            result.append(("share/cxas-scrapi/skills", existing))
    return result


setup(
    name="cxas-scrapi",
    version="0.1.6",
    description="A high level scripting API for CX Agent Studio developers.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/GoogleCloudPlatform/cxas-scrapi",
    author="Patrick Marlow",
    author_email="pmarlow@google.com",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Communications :: Chat",
        "Topic :: Software Development",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    keywords="ces, google, bot, chatbot, agent, scrapi",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.10, <4",
    install_requires=[
        "google-cloud-ces",
        "google-auth",
        "requests",
        "protobuf",
        "google-cloud-secret-manager",
        "pydantic",
        "jsonpath-ng",
        "pandas",
        "gspread",
        "gspread-dataframe",
        "ipython",
        "PyYAML",
        "tqdm",
        "google-genai",
        "pandas-gbq",
        "google-cloud-bigquery",
        "pytest",
        "google-cloud-texttospeech",
        "websocket-client",
        "certifi",
        "json5",
        "graphviz",
        "rich",
        "google-cloud-dialogflow-cx",
    ],
    entry_points={
        "console_scripts": [
            "cxas=cxas_scrapi.cli.main:main",
        ],
    },
    data_files=_collect_data_files(
        [".agents", ".claude", ".gemini"],
        ["AGENTS.md", "GEMINI.md", "examples/cxaslint.yaml"],
    ),
)
