import os from "node:os";
import path from "node:path";
import fs from "node:fs/promises";
import crypto from "node:crypto";
import { execa } from "execa";

export const HOME = os.homedir();
export const STATE_DIR = path.join(HOME, ".gemmaclaw");
export const OPENCLAW_JSON_PATH = path.join(STATE_DIR, "openclaw.json");
export const SYNC_MAP_PATH = path.join(HOME, ".config", "fde", "sync_map.json");
export const MIRRORS_ROOT = path.join(STATE_DIR, "mirrors");

export const GEMMACLAW_BIN_DIR = path.join(HOME, ".nvm", "versions", "node", "v24.15.0", "bin");
export const GEMMACLAW_BIN = path.join(HOME, "dev", "gemmaclaw", "gemmaclaw.mjs");

export async function fileExists(p: string): Promise<boolean> {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

export async function runCommand(
  cmd: string,
  args: string[],
  options: { stream?: boolean; capture?: boolean; env?: Record<string, string> } = {}
): Promise<{ code: number; stdout: string; stderr: string }> {
  const env = { ...process.env, PATH: `${GEMMACLAW_BIN_DIR}:${process.env.PATH}`, ...options.env };
  try {
    const proc = execa(cmd, args, {
      env,
      all: true,
      stdio: options.stream ? "inherit" : "pipe",
    });
    const result = await proc;
    return {
      code: result.exitCode || 0,
      stdout: result.stdout || "",
      stderr: result.stderr || "",
    };
  } catch (err: any) {
    return {
      code: err.exitCode || 1,
      stdout: err.stdout || "",
      stderr: err.message || String(err),
    };
  }
}

export async function ensureDir755(p: string): Promise<void> {
  try {
    await fs.mkdir(p, { recursive: true });
    await fs.chmod(p, 0o755);
  } catch (err: any) {
    if (err.code !== "EPERM" && err.code !== "EACCES") {
      throw err;
    }
  }
}

export async function ensureFile644(p: string): Promise<void> {
  try {
    await fs.chmod(p, 0o644);
  } catch (err: any) {
    if (err.code !== "EPERM" && err.code !== "EACCES") {
      throw err;
    }
  }
}

export function getCitCPathHash(localPath: string): string {
  return crypto.createHash("sha256").update(localPath).digest("hex").slice(0, 10);
}

export async function syncPaths(
  agentId: string,
  direction: "both" | "to_mirror" | "to_citc" = "both",
  writeBack = true
): Promise<void> {
  if (!(await fileExists(SYNC_MAP_PATH))) {
    return;
  }

  const syncMapData = JSON.parse(await fs.readFile(SYNC_MAP_PATH, "utf-8"));
  const agentSync = syncMapData[agentId] || {};
  if (Object.keys(agentSync).length === 0) {
    return;
  }

  const rsyncOpts = [
    "-avz",
    "--exclude",
    ".git",
    "--exclude",
    "node_modules",
    "--exclude",
    "blaze-*",
    "--exclude",
    "readonly",
  ];

  for (const [mirrorPath, citcPath] of Object.entries(agentSync)) {
    const cPath = citcPath as string;
    const mPath = mirrorPath as string;

    if (direction === "both" || direction === "to_mirror") {
      console.log(`🔄 Syncing CitC -> Mirror: ${cPath} -> ${mPath}`);
      await runCommand("rsync", [...rsyncOpts, `${cPath}/`, `${mPath}/`]);
    }

    if ((direction === "both" || direction === "to_citc") && writeBack) {
      console.log(`🔄 Syncing Mirror -> CitC: ${mPath} -> ${cPath}`);
      await runCommand("rsync", [...rsyncOpts, `${mPath}/`, `${cPath}/`]);
    }
  }
}

export async function ensureAgentCredentials(agentId: string): Promise<void> {
  try {
    // 1. Fetch fresh gcloud application default access token
    const res = await runCommand("gcloud", ["auth", "application-default", "print-access-token"]);
    if (res.code !== 0) {
      console.warn("⚠️ Failed to obtain fresh gcloud access token. Skipping auth registration.");
      return;
    }
    const freshToken = res.stdout.trim();

    // 2. Load/merge auth-profiles.json
    const authPath = path.join(STATE_DIR, "agents", agentId, "agent", "auth-profiles.json");
    let mergedData: any = { version: 1, profiles: {} };

    if (await fileExists(authPath)) {
      try {
        mergedData = JSON.parse(await fs.readFile(authPath, "utf-8"));
      } catch {}
    }

    mergedData.profiles = mergedData.profiles || {};
    
    const tokenEntry = {
      type: "token",
      provider: "google-vertex",
      token: freshToken,
    };

    mergedData.profiles["google-vertex:gcloud"] = tokenEntry;
    mergedData.profiles["gcp-vertex-credentials"] = tokenEntry;

    await ensureDir755(path.dirname(authPath));
    await fs.writeFile(authPath, JSON.stringify(mergedData, null, 2), "utf-8");
    await ensureFile644(authPath);
    
    console.log(`Generated and registered fresh google-vertex OAuth token for '${agentId}'`);
  } catch (err) {
    console.error(`⚠️ Error generating credentials for '${agentId}':`, err);
  }
}

export async function getTuiLink(agentId: string): Promise<string> {
  let token = "";
  try {
    if (await fileExists(OPENCLAW_JSON_PATH)) {
      const config = JSON.parse(await fs.readFile(OPENCLAW_JSON_PATH, "utf-8"));
      token = config.gateway?.auth?.token || "";
    }
  } catch {}
  
  return `http://127.0.0.1:9187/chat?agent=${agentId}&session=agent%3A${agentId}%3Adefault&token=${token}`;
}

export async function resolveActiveSetupArgs(agentId: string): Promise<string[]> {
  const defaultArgs = [
    "setup",
    "--agent-name", agentId,
    "--non-interactive"
  ];

  try {
    if (await fileExists(OPENCLAW_JSON_PATH)) {
      const config = JSON.parse(await fs.readFile(OPENCLAW_JSON_PATH, "utf-8"));
      
      // 1. Check Google Vertex AI
      const vertex = config.models?.providers?.["google-vertex"];
      if (vertex) {
        const baseUrl = vertex.baseUrl || "";
        const match = baseUrl.match(/projects\/([a-zA-Z0-9\-_]+)\/locations\/([a-zA-Z0-9\-_]+)/);
        const project = match ? match[1] : "ces-deployment-dev";
        const region = match ? match[2] : "us-west1";
        const model = vertex.models?.[0]?.id || "publishers/google/models/gemma-4-31b-it";

        return [
          "setup",
          "--vertex",
          "--vertex-project", project,
          "--vertex-region", region,
          "--vertex-model", model,
          "--vertex-api-format", "openai",
          "--vertex-dedicated-url", baseUrl,
          "--agent-name", agentId,
          "--non-interactive"
        ];
      }

      // 2. Check Public Gemini API Key
      const gemini = config.models?.providers?.["gemini"] || config.models?.providers?.["google-gemini"];
      if (gemini) {
        const model = gemini.models?.[0]?.id || "google/gemini-3.5-flash";
        return [
          "setup",
          "--setup-mode", "gemini",
          "--model", model,
          "--agent-name", agentId,
          "--non-interactive"
        ];
      }
    }
  } catch {}

  return defaultArgs;
}

export async function printAndCopyTuiLink(agentId: string, label: string = "🚀 Browser TUI Link:"): Promise<string> {
  const link = await getTuiLink(agentId);
  
  let copied = false;
  try {
    const clipboardProc = execa("sh", ["-c", `echo -n "${link}" | (pbcopy || xclip -selection clipboard 2>/dev/null)`]);
    await clipboardProc;
    copied = true;
  } catch {}

  console.log(label);
  console.log(`  ${link}`);
  if (copied) {
    console.log("  📋 (Copied to clipboard automatically!)");
  }
  return link;
}

import { fileURLToPath } from "node:url";
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export async function injectRegistryTemplates(agentId: string, workspaceDir: string): Promise<void> {
  const templatesDir = path.join(__dirname, "..", "templates");
  
  // 1. Prompt Injection (Append to existing markdown files)
  const promptsSrc = path.join(templatesDir, "prompts");
  if (await fileExists(promptsSrc)) {
    try {
      const files = await fs.readdir(promptsSrc);
      for (const file of files) {
        const srcPath = path.join(promptsSrc, file);
        const destPath = path.join(workspaceDir, file);
        
        const appendContent = await fs.readFile(srcPath, "utf-8");
        let originalContent = "";
        if (await fileExists(destPath)) {
          originalContent = await fs.readFile(destPath, "utf-8");
        }
        
        // Prevent duplicate templates accumulation to avoid context limit overflows E2E
        const cleanAppend = appendContent.trim();
        if (originalContent.includes(cleanAppend.substring(0, 100))) {
          console.log(`  🎯 Prompt template already staged in: ${file} (skipped duplicate)`);
          continue;
        }

        const newContent = `${originalContent}\n\n${appendContent}`;
        await fs.writeFile(destPath, newContent, "utf-8");
        console.log(`  🎯 Injected prompt template into: ${file}`);
      }
    } catch (err) {
      console.warn(`  ⚠️ Warning: Failed to inject prompts registry:`, err);
    }
  }

  // 2. File Injection (Copy files or directories recursively)
  const filesSrc = path.join(templatesDir, "files");
  if (await fileExists(filesSrc)) {
    try {
      const files = await fs.readdir(filesSrc);
      for (const file of files) {
        const srcPath = path.join(filesSrc, file);
        const destPath = path.join(workspaceDir, file);
        const stats = await fs.stat(srcPath);
        
        if (stats.isDirectory()) {
          await fs.cp(srcPath, destPath, { recursive: true });
          console.log(`  📂 Injected directory template: ${file}`);
        } else {
          await fs.copyFile(srcPath, destPath);
          console.log(`  📄 Injected file template: ${file}`);
        }
      }
    } catch (err) {
      console.warn(`  ⚠️ Warning: Failed to inject files registry:`, err);
    }
  }

  // 3. Skill Injection (Copy folders to .openclaw/skills/)
  const skillsSrc = path.join(templatesDir, "skills");
  const destSkillsDir = path.join(workspaceDir, ".openclaw", "skills");
  if (await fileExists(skillsSrc)) {
    try {
      const skills = await fs.readdir(skillsSrc, { withFileTypes: true });
      for (const skill of skills) {
        if (skill.isDirectory()) {
          const srcPath = path.join(skillsSrc, skill.name);
          const destPath = path.join(destSkillsDir, skill.name);
          await fs.mkdir(destSkillsDir, { recursive: true });
          await fs.cp(srcPath, destPath, { recursive: true });
          console.log(`  ⚡ Injected skill package: ${skill.name}`);
        }
      }
    } catch (err) {
      console.warn(`  ⚠️ Warning: Failed to inject skills registry:`, err);
    }
  }
}
