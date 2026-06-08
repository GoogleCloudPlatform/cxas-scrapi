import os
import sys
from unittest.mock import MagicMock, patch

# Append scripts directory to sys.path so config can be resolved
scripts_dir = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "../../../.agents/skills/cxas-agent-foundry/scripts",
    )
)
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

# We need to mock config.get_project_path before importing sync-callbacks
# because it calls it at module level.
import config  # noqa: E402

with patch.object(
    config, "get_project_path", return_value="/tmp/mock_project_path"
):
    # Dynamic import of sync-callbacks.py due to hyphen in filename
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sync_callbacks", os.path.join(scripts_dir, "sync-callbacks.py")
    )
    sync_callbacks = importlib.util.module_from_spec(spec)
    sys.modules["sync_callbacks"] = sync_callbacks
    spec.loader.exec_module(sync_callbacks)


def test_sync_agent_callbacks_passes_resource_name():

    with patch("cxas_scrapi.core.callbacks.Callbacks") as mock_callbacks_class:
        mock_callbacks_client = MagicMock()
        mock_callbacks_class.return_value = mock_callbacks_client
        mock_callbacks_client.list_callbacks.return_value = {}

        sync_callbacks.sync_agent_callbacks(
            app_name="projects/P/locations/L/apps/A",
            agent_name="Technical Support",
            agent_resource_name="projects/P/locations/L/apps/A/agents/123",
            dry_run=False,
        )

        mock_callbacks_client.list_callbacks.assert_called_once_with(
            "projects/P/locations/L/apps/A/agents/123"
        )


@patch("sync_callbacks.load_app_name")
@patch("sync_callbacks.sync_agent_callbacks")
def test_main_loop_passes_resource_name(
    mock_sync_agent_callbacks, mock_load_app_name
):
    mock_load_app_name.return_value = "projects/P/locations/L/apps/A"
    mock_sync_agent_callbacks.return_value = (0, 0, 0)

    with patch("cxas_scrapi.core.agents.Agents") as mock_agents_class:
        mock_agents_client = MagicMock()
        mock_agents_class.return_value = mock_agents_client

        mock_agent = MagicMock()
        mock_agent.display_name = "Technical Support"
        mock_agent.name = "projects/P/locations/L/apps/A/agents/123"
        mock_agents_client.list_agents.return_value = [mock_agent]

        mock_args = MagicMock()
        mock_args.agent = None
        mock_args.from_local = None
        mock_args.dry_run = False

        with patch("argparse.ArgumentParser.parse_args") as mock_parse_args:
            mock_parse_args.return_value = mock_args
            sync_callbacks.main()

            mock_sync_agent_callbacks.assert_called_once_with(
                "projects/P/locations/L/apps/A",
                "Technical Support",
                "projects/P/locations/L/apps/A/agents/123",
                dry_run=False,
            )
