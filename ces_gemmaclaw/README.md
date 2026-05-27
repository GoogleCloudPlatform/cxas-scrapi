# CES Gemmaclaw CLI (`cesgemmaclaw` / `cgem`)

The `cesgemmaclaw` CLI provides an out-of-the-box TypeScript-based command line interface matching the FDE CLI (`fde gem`) functionality for configuring, sandboxing, linking, and auto-synchronizing active developer directories for Gemma 4 AI agentic workloads.

## Features

*   **`setup`**: Automatic regional Vertex AI connections verification, permissions hardening (`755`/`644`), background system service gateway installation on port `9187`, and TUI boot.
*   **`create <name>`**: Instantly provision new assistant sandbox agent.
*   **`list`**: Dynamic inventory check of all active agent configurations.
*   **`link`**: Interactive mounting of host Google3 CitC paths to sandboxed mirrors `/workspace/shared/<folder_name>` inside Docker.
*   **`sync <agent>`**: Bidirectional rsync mirroring between sandbox container and your host `google3` CitC workspace.
*   **`message <agent> <text>`**: Direct conversational message.
*   **`tui [agent]`**: Standard local Terminal User Interface launcher.
*   **`remove [name]`**: Full config cleanup, workspace deletion, and mirror prunes.

## Commands and Aliases

Every command invocation automatically outputs a helpful tip showing the shortcut alias:
```bash
💡 Tip: You can also use the alias 'cgem' for this command.
```

Use the command directly or via the `cgem` alias:
```bash
cesgemmaclaw --help
cgem --help
```

---

## Non-Technical Developer Lifecycle Guide

### 1. Perform One-Time Setup
Configure Node/Docker dependencies and Vertex AI credentials:
```bash
cgem setup
```

### 2. Spawn a New Assistant Agent
Spawn an assistant agent named `helper`:
```bash
cgem create helper
```

### 3. Link Your google3 Workspace
Link your active Google3 developer folder into the assistant's sandbox:
```bash
cgem link
```
*Follow the prompts to select `helper` (Index Number) and hit Enter to use your current directory. The CLI mirrors CitC files safely under `~/.gemmaclaw/mirrors/helper` and mounts it to `/workspace/shared/` inside the sandbox.*

### 4. Open TUI & Chat
Connect to the sandboxed assistant:
```bash
cgem tui helper
```
*Ask the assistant to edit, write, or test code directly!*

### 5. Sync Modifications & Push CL
After the assistant finishes modifying code in the sandbox, synchronize all changes back to your host Git/CitC workspace cleanly:
```bash
cgem sync helper
```
*Now run standard `g4 diff` outside the container, verify the edits, and push your CL!*

### 6. Cleanup
Wipe the test assistant:
```bash
cgem remove helper
```

---

## Technical Installation & Build

### Prerequisites
*   Node.js version: `v20` or newer (blessed Node `v24.15.0`)
*   TypeScript `tsc` compiler installed

### Build from Source
```bash
cd ces_gemmaclaw
npm install
npm run build
```

### Global Symlink Registration
To register the commands globally in your terminal shell PATH:
```bash
npm link
```

### Troubleshooting: `env: 'node': No such file or directory`
If running `cgem` in a fresh terminal window returns `env: 'node': No such file or directory`, it means NVM (Node Version Manager) has not yet been loaded in your current terminal session.

To solve this cleanly and permanently for all future terminal windows, add the standard NVM startup script to your shell configuration file (`~/.bashrc` or `~/.zshrc`):
```bash
# Automatically load Node/NVM on shell startup
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
```
Once added, restart your terminal or run `source ~/.bashrc`, and `cgem` will be globally active in all windows!
