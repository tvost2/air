#!/usr/bin/env python3
"""Wire AIR's MCP server into every AI coding client on this machine.

Covers:
  - Claude Code       -- .mcp.json (repo root, "mcpServers" key)
  - VS Code Copilot Chat, this workspace -- .vscode/mcp.json (repo,
    "servers" key, "type": "stdio"); also installs the GitHub.copilot /
    GitHub.copilot-chat extensions if the `code` CLI is available.
  - VS Code Copilot Chat, every workspace -- the *user* mcp.json
    (%APPDATA%/Code/User on Windows, ~/Library/Application Support/Code/User
    on Mac, ~/.config/Code/User on Linux), so `air` is available no matter
    which folder is open, not just this repo. Skip with --skip-global.
  - Codex (CLI + the `openai.chatgpt` VS Code extension, which share
    ~/.codex/config.toml) -- appends/replaces the [mcp_servers.air] table.

DeepSeek (the `vizards.deepseek-v4-for-copilot` extension) is NOT a
separate target: it only registers a model in Copilot Chat's picker
(`languageModelChatProviders` in its package.json) and has no MCP surface
of its own, so wiring VS Code Copilot Chat above already covers it.

Idempotent: re-running only touches the "air" entry in each config; any
other MCP servers already configured are left untouched.

Usage: python scripts/install_mcp.py [--skip-extensions] [--skip-global]
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = shutil.which("python") or shutil.which("python3") or sys.executable


def air_entry(schema: str) -> dict:
    # cwd/storage: VS Code resolves ${workspaceFolder} itself, so the
    # *workspace* mcp.json works unmodified for anyone who opens this repo --
    # no need to have run this installer first. Every other target (Claude
    # Code's .mcp.json, the VS Code *user* mcp.json, Codex) has no such
    # variable and always applies regardless of which folder is open, so
    # those get the resolved absolute path; running this script on another
    # machine recomputes it correctly.
    workspace_scoped = schema == "vscode"
    root = "${workspaceFolder}" if workspace_scoped else str(REPO)
    sep = "/" if workspace_scoped else "\\"
    env = {
        "AIR_STORAGE": f"{root}{sep}storage{sep}air_mcp.db",
        "AIR_LOG_LEVEL": "INFO",
        "AIR_MAX_CONTEXT": "2000",
        "AIR_ENABLE_STRUCTURAL_MEMORY": "true",
    }
    entry = {"command": PYTHON, "args": ["-m", "mcp_server.server"], "cwd": root, "env": env}
    if schema in ("vscode", "vscode_global"):
        # VS Code's mcp.json requires an explicit transport type; Claude
        # Code's .mcp.json infers stdio from the absence of "type"/"url".
        entry = {"type": "stdio", **entry}
    return entry


def find_vscode_user_dir() -> Path:
    """%APPDATA%/Code/User (Win), ~/Library/Application Support/Code/User
    (Mac), ~/.config/Code/User (Linux) -- where VS Code keeps the *user*
    (cross-workspace) mcp.json, distinct from a repo's .vscode/mcp.json."""
    import os

    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "Code" / "User"


def merge_json(path: Path, top_key: str, schema: str) -> None:
    data = json.loads(path.read_text("utf-8")) if path.exists() else {}
    data.setdefault(top_key, {})["air"] = air_entry(schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {path}")


def find_code_cli() -> str | None:
    found = shutil.which("code") or shutil.which("code.cmd")
    if found:
        return found
    # Common Windows install location, in case `code` isn't on PATH for
    # this shell even though VS Code is installed.
    guess = Path.home() / "AppData/Local/Programs/Microsoft VS Code/bin/code.cmd"
    return str(guess) if guess.exists() else None


def install_claude_code() -> None:
    merge_json(REPO / ".mcp.json", "mcpServers", "claude")


def install_vscode_copilot(skip_extensions: bool) -> None:
    # Workspace-level: ships with the repo, so it applies to whoever opens
    # this folder with Copilot Chat -- no per-machine setup needed beyond
    # having the extension. This is also what DeepSeek-for-Copilot rides
    # on (see module docstring).
    merge_json(REPO / ".vscode" / "mcp.json", "servers", "vscode")
    if skip_extensions:
        return
    code_cli = find_code_cli()
    if not code_cli:
        print("  'code' CLI not found -- install the GitHub Copilot Chat "
              "extension manually from the VS Code Extensions view.")
        return
    for ext in ("GitHub.copilot", "GitHub.copilot-chat"):
        subprocess.run([code_cli, "--install-extension", ext], check=False)


def install_vscode_copilot_global() -> None:
    """User-level mcp.json: applies to every VS Code window regardless of
    which folder is open, unlike the workspace .vscode/mcp.json above.
    Per-machine, not shipped with the repo -- same rationale as Codex."""
    merge_json(find_vscode_user_dir() / "mcp.json", "servers", "vscode_global")


def install_codex() -> None:
    """Appends/replaces [mcp_servers.air] in ~/.codex/config.toml.

    User-level, not part of the repo (Codex CLI only reads the global
    config) -- this is the one target that has to run once per machine.
    """
    cfg = Path.home() / ".codex" / "config.toml"
    text = cfg.read_text("utf-8") if cfg.exists() else ""

    # Drop any previous [mcp_servers.air] / [mcp_servers.air.*] tables so
    # re-running this script replaces rather than duplicates the entry.
    text = re.sub(r"\n?\[mcp_servers\.air(\.[a-zA-Z0-9_]+)?\](?:(?!\n\[).)*", "", text, flags=re.S).rstrip("\n")

    entry = air_entry("claude")
    env_lines = "\n".join(f"{k} = {json.dumps(v)}" for k, v in entry["env"].items())
    block = (
        f"\n\n[mcp_servers.air]\n"
        f'command = {json.dumps(entry["command"])}\n'
        f'args = {json.dumps(entry["args"])}\n'
        f'cwd = {json.dumps(entry["cwd"])}\n\n'
        f"[mcp_servers.air.env]\n{env_lines}\n"
    )
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text((text + block).lstrip("\n"), encoding="utf-8")
    print(f"  wrote {cfg}")


def main() -> None:
    skip_extensions = "--skip-extensions" in sys.argv
    skip_global = "--skip-global" in sys.argv

    print("Claude Code (.mcp.json):")
    install_claude_code()

    print("VS Code Copilot Chat + DeepSeek-for-Copilot (.vscode/mcp.json):")
    install_vscode_copilot(skip_extensions)

    if not skip_global:
        print("VS Code Copilot Chat, every workspace (user mcp.json):")
        install_vscode_copilot_global()

    print("Codex CLI + openai.chatgpt extension (~/.codex/config.toml):")
    install_codex()

    print("\nDone. Restart each client (or the current session) to pick up "
          "the new MCP server -- none of them reconnect retroactively.")


if __name__ == "__main__":
    main()
