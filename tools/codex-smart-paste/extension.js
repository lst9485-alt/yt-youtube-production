"use strict";

const cp = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");
const vscode = require("vscode");

function activate(context) {
  const output = vscode.window.createOutputChannel("Codex Smart Paste");

  const disposable = vscode.commands.registerCommand(
    "codexSmartPaste.pasteToTerminal",
    async () => {
      const pastedImagePath = await tryPasteImagePath(output);
      if (pastedImagePath) {
        await vscode.commands.executeCommand("workbench.action.terminal.sendSequence", {
          text: pastedImagePath,
        });
        return;
      }

      await vscode.commands.executeCommand("workbench.action.terminal.paste");
    }
  );

  context.subscriptions.push(disposable, output);
}

async function tryPasteImagePath(output) {
  if (process.platform !== "darwin") {
    return null;
  }

  const config = vscode.workspace.getConfiguration("codexSmartPaste");
  const tempDir = config.get("tempDir", "/tmp/codex-paste-images");
  const outputPath = path.join(tempDir, `${timestamp()}.png`);
  const scriptPath = path.join(__dirname, "scripts", "clipboard-image-to-png.swift");

  await fs.mkdir(tempDir, { recursive: true });
  await fs.mkdir("/tmp/swift-module-cache", { recursive: true });

  try {
    const { stdout, stderr } = await execFile("swift", [
      "-module-cache-path",
      "/tmp/swift-module-cache",
      scriptPath,
      outputPath,
    ], {
      env: {
        ...process.env,
        CLANG_MODULE_CACHE_PATH: "/tmp/swift-module-cache",
      },
    });

    if (stderr.trim()) {
      output.appendLine(stderr.trim());
    }

    const parsed = parseResult(stdout);
    if (!parsed.ok || !parsed.path) {
      return null;
    }

    return parsed.path;
  } catch (error) {
    output.appendLine(formatError(error));
    return null;
  }
}

function timestamp() {
  const now = new Date();
  const year = String(now.getFullYear());
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  const hours = String(now.getHours()).padStart(2, "0");
  const minutes = String(now.getMinutes()).padStart(2, "0");
  const seconds = String(now.getSeconds()).padStart(2, "0");
  return `${year}${month}${day}-${hours}${minutes}${seconds}`;
}

function parseResult(stdout) {
  const raw = stdout.trim();
  if (!raw) {
    return { ok: false };
  }

  try {
    return JSON.parse(raw);
  } catch {
    return { ok: false };
  }
}

function formatError(error) {
  if (error instanceof Error) {
    return error.message;
  }

  return String(error);
}

function execFile(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    cp.execFile(command, args, { encoding: "utf8", ...options }, (error, stdout, stderr) => {
      if (error) {
        reject(error);
        return;
      }

      resolve({ stdout, stderr });
    });
  });
}

function deactivate() {}

module.exports = {
  activate,
  deactivate,
};
