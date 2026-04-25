---
title: Installation
description: How to install cxas-scrapi and verify your setup.
---

# Installation

Installing CXAS SCRAPI is straightforward — it's a standard Python package on PyPI. This page walks you through the requirements, the install command, how to verify everything is working, and a few best practices that'll save you headaches later.

---

## Requirements

Before you install, make sure your environment meets these requirements:

| Requirement | Version | Notes |
|---|---|---|
| **Python** | 3.10 or newer | Check with `python --version` or `python3 --version` |
| **pip** | Any recent version | Usually comes with Python |
| **gcloud CLI** | Any recent version | Recommended for local auth; not strictly required |

!!! tip "Why Python 3.10?"
    SCRAPI uses several Python 3.10+ features — including structural pattern matching and more precise type hints — to keep the code clean and maintainable. If you're on an older Python, now is a great time to upgrade.

To check your Python version:

```sh
python --version
# or
python3 --version
```

---

## Set up a virtual environment (recommended)

It's a good habit to install Python packages inside a virtual environment rather than into your global Python installation. This keeps your projects isolated from each other and makes it easy to manage dependencies.

=== "venv (built-in)"

    ```sh
    # Create a virtual environment called ".venv"
    python -m venv .venv

    # Activate it
    # On macOS / Linux:
    source .venv/bin/activate

    # On Windows:
    .venv\Scripts\activate
    ```

=== "conda"

    ```sh
    conda create -n cxas-scrapi python=3.11
    conda activate cxas-scrapi
    ```

Once your environment is active, you'll see the environment name in your terminal prompt. Everything you install from here goes into that environment, not your system Python.

---

## Install from PyPI

With your virtual environment active, run:

```sh
pip install cxas-scrapi
```

That's it. pip will download and install `cxas-scrapi` and all of its dependencies automatically.

!!! note "Installation may take a moment"
    SCRAPI pulls in the official `google-cloud-ces` client and several other Google Cloud libraries. The first install can take 30–60 seconds depending on your connection speed.

---

## Install from source

If you want to work with the latest unreleased code, you can install directly from the GitHub repository. If you're contributing to SCRAPI, see the [Development Setup](../contributing/development-setup.md) guide instead.

```sh
# Clone the repository
git clone https://github.com/GoogleCloudPlatform/cxas-scrapi.git
cd cxas-scrapi

# Install in editable mode (changes to the source are reflected immediately)
pip install -e .
pip install -r requirements.txt
```

The `-e` flag (editable install) means Python points directly to the source files in your cloned directory, so any edits you make are picked up immediately without reinstalling.

---

## Verify your installation

After installing, check that the `cxas` CLI is available:

```sh
cxas --help
```

You should see output like this:

```
usage: cxas [-h] {pull,push,create,delete,branch,apps,export,push-eval,run,
             test-tools,test-callbacks,ci-test,local-test,
             init-github-action,lint,init,insights} ...

CX Agent Studio Scripting API CLI

positional arguments:
  {pull,push,create,...}
    pull                Pull agent config from platform
    push                Push agent config to platform
    lint                Lint agent configuration files
    ...

options:
  -h, --help  show this help message and exit
```

You can also verify the Python import works:

```python
import cxas_scrapi
print("CXAS SCRAPI is installed and ready!")
```

---

## Install the gcloud CLI (recommended)

SCRAPI's most convenient authentication method — Application Default Credentials (ADC) — requires the gcloud CLI. If you're doing local development, installing gcloud is strongly recommended.

Follow the [official gcloud CLI installation guide](https://cloud.google.com/sdk/docs/install) for your platform, then run:

```sh
gcloud init
gcloud auth application-default login
```

After that, SCRAPI will automatically pick up your credentials. See [Authentication](authentication.md) for full details.

---

## What's next?

Now that SCRAPI is installed, you need credentials so it can talk to Google Cloud:

[Authentication →](authentication.md){ .md-button .md-button--primary }

Or, if you already have authentication sorted out, jump straight to a quickstart:

[Python Quickstart →](quickstart-python.md){ .md-button }
[CLI Quickstart →](quickstart-cli.md){ .md-button }
