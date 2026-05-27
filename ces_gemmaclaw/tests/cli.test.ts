import { describe, it, expect, vi, beforeEach } from "vitest";
import fs from "node:fs/promises";
import path from "node:path";
import {
  fileExists,
  getCitCPathHash,
  STATE_DIR,
  OPENCLAW_JSON_PATH,
} from "../src/utils.js";

describe("cesgemmaclaw CLI Utilities", () => {
  it("fileExists returns true for existing files", async () => {
    const existing = await fileExists(process.cwd());
    expect(existing).toBe(true);
  });

  it("fileExists returns false for missing files", async () => {
    const missing = await fileExists(path.join(process.cwd(), "missing-file-xyz.json"));
    expect(missing).toBe(false);
  });

  it("getCitCPathHash generates stable 10-char hash", () => {
    const testPath = "/google/src/cloud/user/workspace/google3/project";
    const hash1 = getCitCPathHash(testPath);
    const hash2 = getCitCPathHash(testPath);

    expect(hash1).toHaveLength(10);
    expect(hash1).toBe(hash2);
  });

  it("STATE_DIR resolves correctly under user home directory", () => {
    expect(STATE_DIR).toContain(".gemmaclaw");
  });

  it("OPENCLAW_JSON_PATH resolves openclaw.json config location", () => {
    expect(OPENCLAW_JSON_PATH).toContain("openclaw.json");
  });
});
