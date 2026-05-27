#!/usr/bin/env node
import { Command } from "commander";
import inquirer from "inquirer";
import fs from "node:fs/promises";
import path from "node:path";
import {
  STATE_DIR,
  OPENCLAW_JSON_PATH,
  SYNC_MAP_PATH,
  MIRRORS_ROOT,
  GEMMACLAW_BIN,
  fileExists,
  runCommand,
  ensureDir755,
  ensureFile644,
  getCitCPathHash,
  syncPaths,
  ensureAgentCredentials,
} from "./utils.js";

// Print the Tip Banner first
console.log("💡 Tip: You can also use the alias 'cgem' for this command.");

const program = new Command();

program
  .name("cesgemmaclaw")
  .description("CES Gemmaclaw CLI - forward deployed engineering task automation");

program
  .command("setup")
  .description("One-time setup to provision assistant backends, verify sandbox environment, and start background gateway.")
  .option("-b, --backend <type>", "Backend model provider: gemini | vertex | google-internal")
  .option("--vertex-project <id>", "GCP Project ID (for Vertex AI)")
  .option("--vertex-region <name>", "GCP Region (for Vertex AI)")
  .option("--vertex-model <name>", "Gemma model on Vertex AI")
  .option("--vertex-dedicated-url <url>", "Dedicated vLLM prediction endpoint URL")
  .option("--non-interactive", "Skip interactive prompts and run with defaults")
  .option("--accept-risk", "Acknowledge system-access risk")
  .action(async (options) => {
    let backend = options.backend;

    if (!backend && !options.nonInteractive) {
      const { choice } = await inquirer.prompt([
        {
          type: "list",
          name: "choice",
          message: "Select a backend model provider setup:",
          choices: [
            {
              name: "1. Google Gemini API Key (Default - Public API Key)",
              value: "gemini",
            },
            {
              name: "2. Google Cloud Vertex AI (GCP Project with ADC/Service Account)",
              value: "vertex",
            },
            {
              name: "3. Google Internal (Google Corp Network ONLY - FDE dedicated bridge)",
              value: "google-internal",
            },
          ],
        },
      ]);
      backend = choice;
    } else if (!backend) {
      backend = "gemini"; // Default non-interactive backend choice
    }

    let setupArgs: string[] = ["setup"];

    if (backend === "gemini") {
      console.log("🚀 Starting Google Gemini Public API setup...");
      setupArgs.push("--setup-mode", "gemini");
    } else if (backend === "vertex") {
      console.log("🚀 Starting Google Cloud Vertex AI setup...");
      
      let project = options.vertexProject;
      let region = options.vertexRegion;
      let model = options.vertexModel;
      
      if (!options.nonInteractive) {
        const answers = await inquirer.prompt([
          {
            type: "input",
            name: "project",
            message: "Enter GCP Project ID:",
            validate: (val) => val.trim().length > 0 || "Project ID is required",
          },
          {
            type: "input",
            name: "region",
            message: "Enter GCP Region:",
            default: "us-central1",
          },
          {
            type: "input",
            name: "model",
            message: "Enter Gemma model ID (e.g., gemma-3-27b-it):",
            default: "gemma-3-27b-it",
          },
        ]);
        project = answers.project;
        region = answers.region;
        model = answers.model;
      }

      if (!project) {
        console.error("Error: GCP Project ID is required for Vertex setup.");
        process.exit(1);
      }

      setupArgs.push(
        "--vertex",
        "--vertex-project", project,
        "--vertex-region", region || "us-central1",
        "--vertex-model", model || "gemma-3-27b-it"
      );
      if (options.vertexDedicatedUrl) {
        setupArgs.push("--vertex-dedicated-url", options.vertexDedicatedUrl);
      }
    } else if (backend === "google-internal") {
      console.log("⚠️ Note: Google Internal option ONLY works for Google employees inside the Google corp network environment.");
      console.log("🚀 Configuring FDE dedicated internal benchmark bridge...");

      const project = options.vertexProject || "ces-deployment-dev";
      const region = options.vertexRegion || "us-west1";
      const model = options.vertexModel || "gemma-4-31b-it";
      const dedicatedUrl = options.vertexDedicatedUrl || "https://us-west1-aiplatform.googleapis.com/v1/projects/555355609568/locations/us-west1/endpoints/8754771176212922368";

      setupArgs.push(
        "--vertex",
        "--vertex-project", project,
        "--vertex-region", region,
        "--vertex-model", model,
        "--vertex-api-format", "openai",
        "--vertex-dedicated-url", dedicatedUrl
      );
    }

    if (options.acceptRisk) setupArgs.push("--accept-risk");
    if (options.nonInteractive) setupArgs.push("--non-interactive");

    console.log("Provisioning container sandbox...");
    const setupRes = await runCommand(GEMMACLAW_BIN, setupArgs, { stream: true });
    if (setupRes.code !== 0) {
      console.error("❌ Setup failed.");
      process.exit(setupRes.code);
    }

    // Apply local gateway.mode configuration
    await runCommand(GEMMACLAW_BIN, ["config", "set", "gateway.mode", "local"]);

    // Fix directory permissions to prevent sandbox lockout
    const walkPermissions = async (dir: string) => {
      try {
        await ensureDir755(dir);
        const entries = await fs.readdir(dir, { withFileTypes: true });
        for (const entry of entries) {
          const fullPath = path.join(dir, entry.name);
          if (entry.isDirectory()) {
            await walkPermissions(fullPath);
          } else {
            await ensureFile644(fullPath);
          }
        }
      } catch (err: any) {
        if (err.code !== "EPERM" && err.code !== "EACCES") {
          throw err;
        }
      }
    };
    await walkPermissions(STATE_DIR);

    // Generate and stash Vertex credentials for main
    await ensureAgentCredentials("main");

    // Reinstall systemd gateway daemon service on port 9187
    console.log("🚀 Installing background system service (systemd) on port 9187...");
    await runCommand(GEMMACLAW_BIN, ["gateway", "install", "--port", "9187", "--force"]);

    console.log("✅ Background system service successfully configured and started.");
    console.log("TUI available: cesgemmaclaw tui");
  });

program
  .command("create")
  .argument("<name>", "Name of the assistant (e.g. 'bob')")
  .description("Create a new assistant agent configuration.")
  .action(async (name) => {
    const agentId = name.trim().toLowerCase();
    console.log(`Spawn new assistant agent '${agentId}'...`);

    const workspacePath = path.join(STATE_DIR, "workspaces", agentId);
    const createRes = await runCommand(GEMMACLAW_BIN, [
      "agents",
      "add",
      agentId,
      "--non-interactive",
      "--workspace",
      workspacePath,
    ]);
    if (createRes.code !== 0) {
      console.error(`❌ Failed to create agent: ${createRes.stderr}`);
      process.exit(createRes.code);
    }

    // Copy credentials from the main agent auth profiles (if available)
    const mainAuthPath = path.join(STATE_DIR, "agents", "main", "agent", "auth-profiles.json");
    const newAuthPath = path.join(STATE_DIR, "agents", agentId, "agent", "auth-profiles.json");
    
    if (await fileExists(mainAuthPath)) {
      await fs.mkdir(path.dirname(newAuthPath), { recursive: true });
      await fs.copyFile(mainAuthPath, newAuthPath);
      await ensureFile644(newAuthPath);
      console.log(`Inherited auth profiles from 'main' to '${agentId}' (merged).`);
    }

    // Dynamically refresh and register OAuth credentials for this agent
    await ensureAgentCredentials(agentId);

    console.log(`✨ Assistant agent '${agentId}' created successfully.`);
  });

program
  .command("list")
  .description("List all configured Gemmaclaw agents.")
  .action(async () => {
    const listRes = await runCommand(GEMMACLAW_BIN, ["list"]);
    console.log(listRes.stdout);
  });

program
  .command("link")
  .description("Link a local workspace folder into the assistant's container sandbox.")
  .option("-a, --agent <id>", "Agent ID to link")
  .option("-p, --path <path>", "Local directory path to link")
  .action(async (options) => {
    // Fetch agents list
    const listRes = await runCommand(GEMMACLAW_BIN, ["agents", "list", "--json"]);
    if (listRes.code !== 0) {
      console.error("❌ Failed to list agents.");
      process.exit(1);
    }
    
    const agents = JSON.parse(listRes.stdout.slice(listRes.stdout.indexOf("[")));
    if (agents.length === 0) {
      console.error("No agents found. Please create one first using 'cesgemmaclaw create <name>'.");
      process.exit(1);
    }

    let agentId = options.agent;
    if (!agentId) {
      const { idx } = await inquirer.prompt([
        {
          type: "list",
          name: "idx",
          message: "Select an agent to link:",
          choices: agents.map((a: any, i: number) => ({
            name: `${i + 1}. ${a.id}`,
            value: i,
          })),
        },
      ]);
      agentId = agents[idx].id;
    }

    const agent = agents.find((a: any) => a.id === agentId);
    if (!agent) {
      console.error(`Error: Agent '${agentId}' not found.`);
      process.exit(1);
    }

    const cwd = process.cwd();
    let localPath = options.path;
    if (!localPath) {
      const { pathInput } = await inquirer.prompt([
        {
          type: "input",
          name: "pathInput",
          message: `Enter local path to link [Default: ${cwd}]:`,
          default: cwd,
        },
      ]);
      localPath = pathInput;
    }

    const absPath = path.resolve(localPath.trim());
    if (!(await fileExists(absPath)) || !(await (await fs.stat(absPath)).isDirectory())) {
      console.error(`Error: ${absPath} is not a directory.`);
      process.exit(1);
    }

    const basename = path.basename(absPath);
    let effectivePath = absPath;

    // CitC Mirroring Sync Check
    if (absPath.startsWith("/google/src/cloud/")) {
      const pathHash = getCitCPathHash(absPath);
      const mirrorPath = path.join(MIRRORS_ROOT, agent.id, `${basename}_${pathHash}`);
      console.log(`💡 CitC path detected. Creating mirror at: ${mirrorPath}`);

      await ensureDir755(path.dirname(mirrorPath));
      await ensureDir755(mirrorPath);

      // Initial sync CitC -> Mirror
      console.log("🔄 Performing initial sync...");
      await runCommand("rsync", [
        "-avz",
        "--exclude", ".git",
        "--exclude", "node_modules",
        "--exclude", "blaze-*",
        "--exclude", "readonly",
        `${absPath}/`,
        `${mirrorPath}/`,
      ]);

      effectivePath = mirrorPath;

      // Save to sync_map.json
      let syncMap: Record<string, Record<string, string>> = {};
      if (await fileExists(SYNC_MAP_PATH)) {
        syncMap = JSON.parse(await fs.readFile(SYNC_MAP_PATH, "utf-8"));
      }
      
      syncMap[agent.id] = syncMap[agent.id] || {};
      syncMap[agent.id][mirrorPath] = absPath;

      await ensureDir755(path.dirname(SYNC_MAP_PATH));
      await fs.writeFile(SYNC_MAP_PATH, JSON.stringify(syncMap, null, 2), "utf-8");
    }

    const bindMapping = `${effectivePath}:/workspace/shared/${basename}:rw`;

    // Update openclaw.json
    const openclawConfig = JSON.parse(await fs.readFile(OPENCLAW_JSON_PATH, "utf-8"));
    let found = false;

    for (const agentConfig of openclawConfig.agents?.list || []) {
      if (agentConfig.id === agent.id) {
        agentConfig.sandbox = agentConfig.sandbox || {};
        agentConfig.sandbox.docker = agentConfig.sandbox.docker || {};
        agentConfig.sandbox.docker.binds = agentConfig.sandbox.docker.binds || [];

        if (!agentConfig.sandbox.docker.binds.includes(bindMapping)) {
          agentConfig.sandbox.docker.binds.push(bindMapping);
          console.log(`Added bind mount: ${bindMapping}`);
        } else {
          console.log(`Bind mount already exists: ${bindMapping}`);
        }
        found = true;
        break;
      }
    }

    if (!found) {
      console.error(`❌ Agent ${agent.id} not found in openclaw.json list.`);
      process.exit(1);
    }

    await fs.writeFile(OPENCLAW_JSON_PATH, JSON.stringify(openclawConfig, null, 2), "utf-8");
    console.log(`Successfully updated ${OPENCLAW_JSON_PATH}`);
    console.log("✅ Link complete. Please restart the gateway ('systemctl --user restart openclaw-gateway.service') to apply mounts.");
  });

program
  .command("sync")
  .argument("<agent_id>", "Agent ID to synchronize mirrors for")
  .description("Synchronize linked CitC directories with container mirrors.")
  .action(async (agentId) => {
    console.log(`🔄 Synchronizing workspace folders for '${agentId}'...`);
    await syncPaths(agentId, "both", true);
    console.log(`✅ Sync complete for agent '${agentId}'`);
  });

program
  .command("message")
  .argument("<agent>", "Agent ID to send a message to")
  .argument("<text>", "Message text")
  .description("Send a direct message turn to a named agent session.")
  .action(async (agent, text) => {
    console.log(`Sending message to agent '${agent}'...`);
    const msgRes = await runCommand(GEMMACLAW_BIN, ["message", "--agent", agent, text], { stream: true });
    process.exit(msgRes.code);
  });

program
  .command("tui")
  .argument("[agent]", "Named agent ID to launch TUI shell for")
  .description("Open a local Terminal User Interface (TUI) for a named agent.")
  .action(async (agent) => {
    const args = ["tui"];
    if (agent) {
      args.push("--agent", agent);
    }
    console.log("Opening TUI session...");
    const tuiRes = await runCommand(GEMMACLAW_BIN, args, { stream: true });
    process.exit(tuiRes.code);
  });

program
  .command("sandbox")
  .argument("<subcommand>", "Sandbox subcommand: recreate")
  .description("Manage agent Docker container sandboxes.")
  .action(async (subcommand) => {
    if (subcommand === "recreate") {
      console.log("Recreating sandbox environments...");
      const recreationRes = await runCommand(GEMMACLAW_BIN, ["sandbox", "recreate"], { stream: true });
      process.exit(recreationRes.code);
    } else {
      console.error(`Unknown sandbox command: ${subcommand}`);
      process.exit(1);
    }
  });

program
  .command("remove")
  .argument("[name]", "Optional name of the agent to delete")
  .description("Permanently delete an agent configuration, workspace, and mirror mappings.")
  .action(async (name) => {
    let agentId = name;
    
    if (!agentId) {
      const listRes = await runCommand(GEMMACLAW_BIN, ["agents", "list", "--json"]);
      const agents = JSON.parse(listRes.stdout.slice(listRes.stdout.indexOf("[")));
      if (agents.length === 0) {
        console.log("No agents found.");
        process.exit(0);
      }

      const { idx } = await inquirer.prompt([
        {
          type: "list",
          name: "idx",
          message: "Select an agent to remove:",
          choices: agents.map((a: any, i: number) => ({
            name: `${i + 1}. ${a.id}`,
            value: i,
          })),
        },
      ]);
      agentId = agents[idx].id;
    }

    const { confirm } = await inquirer.prompt([
      {
        type: "confirm",
        name: "confirm",
        message: `Are you sure you want to permanently delete agent '${agentId}'?`,
        default: false,
      },
    ]);

    if (!confirm) {
      console.log("Deletion cancelled.");
      process.exit(0);
    }

    console.log(`🗑️ Deleting agent '${agentId}'...`);
    
    // Run gemmaclaw agents delete
    const delRes = await runCommand(GEMMACLAW_BIN, ["agents", "delete", agentId!, "--force"], { stream: true });
    
    // Prune mirrors sync map mappings
    if (await fileExists(SYNC_MAP_PATH)) {
      const syncMap = JSON.parse(await fs.readFile(SYNC_MAP_PATH, "utf-8"));
      if (syncMap[agentId!]) {
        delete syncMap[agentId!];
        await fs.writeFile(SYNC_MAP_PATH, JSON.stringify(syncMap, null, 2), "utf-8");
        console.log(`Removed agent '${agentId}' from mirrors sync map.`);
      }
    }

    // Clean up the mirrors subfolder if it exists
    const mirrorDir = path.join(MIRRORS_ROOT, agentId!);
    if (await fileExists(mirrorDir)) {
      await fs.rm(mirrorDir, { recursive: true, force: true });
      console.log(`Pruned mirrors directory: ${mirrorDir}`);
    }

    console.log(`✨ Agent '${agentId}' removed successfully.`);
    process.exit(delRes.code);
  });

program.parse(process.argv);
