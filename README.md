# CX Agent Studio Scripting API (CXAS SCRAPI)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE.txt)
[![PyPI](https://img.shields.io/pypi/v/cxas-scrapi)](https://pypi.org/project/cxas-scrapi/)
[![Python Unit Tests](https://github.com/google/cxas-scrapi/actions/workflows/python-unit-tests.yml/badge.svg)](https://github.com/google/cxas-scrapi/actions/workflows/python-unit-tests.yml)

<html>
    <h2 align="center">
      <!-- Replace with actual image path once uploaded -->
      <img src="assets/cxas-scrapi-logo.png" width="256" alt="CXAS SCRAPI Logo"/>
    </h2>
    <h3 align="center">
      A powerful Python API, CLI, and set of Agent Skills for CX Agent Studio to automate, evaluate, and scale your agents with ease.
    </h3>
    <h3 align="center">
      Important Links:
      <a href="docs/">Docs</a>,
      <a href="examples/">Examples</a>,
      <a href=".agents/skills/">Agent Skills</a>,
      <a href="src/cxas_scrapi/core/">Core SDK</a>
    </h3>
</html>

CX Agent Studio Scripting API (CXAS SCRAPI) is an open-source Python scripting
API, CLI, and set of Agent Skills for CX Agent Studio. It is designed to
simplify building, deploying, and orchestrating agent workflows, from simple
tasks to complex systems. It integrates seamlessly with Agentic IDEs like
Gemini CLI, Claude Code, and Antigravity, exposing advanced tooling for deep
evaluations, real-time latency metrics, offline linting, and conversation
history.

---

## Built With
* Python 3.11+


<!-- AUTHENTICATION -->
# Authentication  
Authentication can vary depending on how and where you are interacting with SCRAPI.

## Google Colab
If you're using CXAS SCRAPI with a [Google Colab](https://colab.research.google.com/) notebook, you can add the following to the top of your notebook for easy authentication:
```py
project_id = '<YOUR_GCP_PROJECT_ID>'

# this will launch an interactive prompt that allows you to auth with GCP in a browser
!gcloud auth application-default login --no-launch-browser

# this will set your active project to the `project_id` above
!gcloud auth application-default set-quota-project $project_id
```

After running the above, Colab will pick up your credentials from the environment and pass them to CXAS SCRAPI directly. No need to use Service Account keys!
You can then use CXAS SCRAPI simply like this:
```py
from cxas_scrapi import Apps

project_id = '<YOUR_GCP_PROJECT_ID>'
location = 'us'

app_client = Apps(project_id=project_id, location=location) # <-- Creds will be automatically picked up from the environment
apps_map = app_client.get_apps_map()
```
---
## Cloud Functions / Cloud Run
If you're using CXAS SCRAPI with [Cloud Functions](https://cloud.google.com/functions) or [Cloud Run](https://cloud.google.com/run), CXAS SCRAPI can pick up on the default environment creds used by these services without any additional configuration! 

1. Add `cxas-scrapi` to your `requirements.txt` file
2. Ensure the Cloud Function / Cloud Run service account has the appropriate Custom Agent / Conversational Agents IAM Role

Once you are setup with the above, your function code can be used easily like this:
```py
from cxas_scrapi import Agents

app_name = '<YOUR_APP_NAME>'
a = Agents(project_id='<YOUR_GCP_PROJECT_ID>', location='global')
agents_map = a.get_agents_map(app_name)
```

---
## Local Python Environment
Similar to Cloud Functions / Cloud Run, CXAS SCRAPI can pick up on your local authentication creds _if you are using the gcloud CLI._

1. Install [gcloud CLI](https://cloud.google.com/sdk/docs/install).
2. Run `gcloud init`.
3. Run `gcloud auth login`
4. Run `gcloud auth application-default login`
5. Run `gcloud auth list` to ensure your principal account is active.

This will authenticate your principal GCP account with the gcloud CLI, and SCRAPI can pick up the creds from here.  

---
## Exceptions and Misc.
If you prefer to explicitly assign Service Account credentials programmatically instead of relying on the environmental `application-default`, you can pass the path to your JSON key using `creds_path`.

```py
from cxas_scrapi import Tools

creds_path = '<PATH_TO_YOUR_SERVICE_ACCOUNT_JSON_FILE>'

t = Tools(project_id='<YOUR_GCP_PROJECT_ID>', location='global', creds_path=creds_path)
tools_map = t.get_tools_map('<YOUR_APP_NAME>')
```

<!-- GETTING STARTED -->
# Getting Started
## Environment Setup
Set up Google Cloud Platform credentials and install dependencies.
```sh
gcloud auth login
gcloud auth application-default login
gcloud config set project <project name>
```
```sh
python3 -m venv venv
source ./venv/bin/activate
pip install -r requirements.txt
```

## Usage
To run a simple bit of code you can do the following:
- Import a Class from `cxas_scrapi`
- Define your GCP Project and Location

```python
from cxas_scrapi import Apps

# Instantiate your class object and pass in your credentials
app_client = Apps(project_id='<YOUR_GCP_PROJECT_ID>', location='global')

# Retrieve all Apps existing in your project
apps = app_client.list_apps()
for app in apps:
    print(app.display_name, app.name)
```

# Library Composition
Here is a brief overview of the CXAS SCRAPI library's structure and the motivation behind that structure.

## [Core](src/cxas_scrapi/core)
The `src/cxas_scrapi/core` directory contains the high level building blocks of CXAS SCRAPI, mapped to core resource types in the CXAS environment (Apps, Agents, Tools, Guardrails, Deployments, Sessions, etc.)

## [Utils](src/cxas_scrapi/utils)
The `src/cxas_scrapi/utils` directory contains helper functions and background logic for pagination, response flattening, proto conversions, and external integrations like Google Sheets.

## [Evals](src/cxas_scrapi/evals)
The `src/cxas_scrapi/evals` directory provides tools for executing and analyzing agent performance evaluations, including Golden tests and simulation runs, and extracting metrics like latency.

## [CLI](src/cxas_scrapi/cli)
The `src/cxas_scrapi/cli` directory implements the command line interface for SCRAPI, offering tools like `cxas lint` to automate development and validation workflows.

## [Migration](src/cxas_scrapi/migration)
The `src/cxas_scrapi/migration` directory contains tools to facilitate transitioning legacy Dialogflow CX agents to CXAS, including agent generation from flows and artifact building.

<!-- DOCUMENTATION -->
# Documentation

The full documentation site is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/). To run it locally:

```sh
# Install docs dependencies (inside your virtualenv)
pip install -r requirements-docs.txt

# Install the package so API reference pages can render
pip install -e .

# Start the local dev server
mkdocs serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser. Changes to files in `docs/` will reload automatically.

To build the static site without serving:

```sh
mkdocs build          # output goes to site/
mkdocs build --strict # also fails on warnings (used in CI)
```

<!-- CONTRIBUTING -->
# Contributing
We welcome any contributions or feature requests you would like to submit!

1. Fork the Project
2. Create your Feature Branch (git checkout -b feature/AmazingFeature)
3. Commit your Changes (git commit -m 'Add some AmazingFeature')
4. Push to the Branch (git push origin feature/AmazingFeature)
5. Open a Pull Request

<!-- LICENSE -->
# License
Distributed under the Apache 2.0 License. See [LICENSE](LICENSE.txt) for more information.

<!-- CONTACT -->
# Contact
Patrick Marlow - [pmarlow@google.com](mailto:pmarlow@google.com) - [@kmaphoenix](https://github.com/kmaphoenix)  

Project Link: [https://github.com/GoogleCloudPlatform/cxas-scrapi](https://github.com/GoogleCloudPlatform/cxas-scrapi)

<!-- ACKNOWLEDGEMENTS -->
# Acknowledgements
[Google Cloud Customer Engagement AI](https://docs.cloud.google.com/customer-engagement-ai/conversational-agents/ps)
