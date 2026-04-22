---
title: Migration Tools
---

# Migration Tools

The `cxas_scrapi.migration` module provides visualization and dependency analysis tools to help you understand and migrate **Dialogflow CX (DFCX)** agents into **CX Agent Studio (CES)**. If you're in the middle of a DFCX-to-CES migration, these classes can save you hours of manual diagram work.

The tools work with exported DFCX agent data (JSON) and render rich visualizations either in the terminal via the `rich` library or as interactive SVG/HTML diagrams in Jupyter notebooks and Colab.

## Classes at a Glance

| Class | Module | What it does |
|---|---|---|
| `FlowDependencyResolver` | `flow_visualizer` | Traverses a DFCX flow, finds all related intents, entities, tools, and sub-flows. |
| `FlowTreeVisualizer` | `flow_visualizer` | Renders a detailed `rich` tree showing a flow's pages, routes, and handlers. |
| `HighLevelGraphVisualizer` | `graph_visualizer` | Generates a macroscopic directed graph (via Graphviz) matching the DFCX UI topology. |
| `PlaybookTreeVisualizer` | `playbook_visualizer` | Renders a `rich` tree for DFCX Playbook-style agents. |
| `MainVisualizer` | `main_visualizer` | Orchestrates all the above into a single interactive zoom UI for Jupyter / Colab. |

## Quick Example

```python
import json
from cxas_scrapi.migration.main_visualizer import MainVisualizer
from cxas_scrapi.migration.flow_visualizer import FlowDependencyResolver

# Load your exported DFCX agent JSON
with open("my_dfcx_agent_export.json") as f:
    agent_data = json.load(f)

# Resolve dependencies for a specific flow
resolver = FlowDependencyResolver(full_agent_data=agent_data)
flow_name = "Default Start Flow"
selected_data = resolver.resolve(flow_name=flow_name)

# Visualize everything in a Jupyter notebook
visualizer = MainVisualizer(selected_data=selected_data)
visualizer.display()
```

## Reference

::: cxas_scrapi.migration.flow_visualizer.FlowDependencyResolver

::: cxas_scrapi.migration.flow_visualizer.FlowTreeVisualizer

::: cxas_scrapi.migration.graph_visualizer.HighLevelGraphVisualizer

::: cxas_scrapi.migration.playbook_visualizer.PlaybookTreeVisualizer

::: cxas_scrapi.migration.main_visualizer.MainVisualizer
