"""Runs the user's own agent (Claude Code or Codex) to summarize changes. Never crashes lineup."""

import json
import os
import shutil
import subprocess
import tempfile

from lineup import store

# One line per agent. A new agent = one new line here.
AGENTS = {
    "claude": ["claude", "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
               "--strict-mcp-config", "--model", "haiku"],
    "codex": ["codex", "exec", "--json", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check",
              "-m", "gpt-5.4-mini", "-"],
}
LOOP_GUARD = "LINEUP_RUNNING"  # hooks exit when they see this, so the AI call can't trigger them again
TIMEOUT_SECONDS = 120
MAX_LINE = 200
MAX_OVERVIEW = 600


def pick_agent(table, which=shutil.which):
    """The agent saved in the photo, else the first one installed. None if there's none."""
    saved = (table or {}).get("agent")
    if saved in AGENTS:
        return saved
    return next((name for name in AGENTS if which(AGENTS[name][0])), None)


def _reply_text(agent, stdout):
    """The AI's own text from the agent's JSON output, or None."""
    if agent == "claude":
        data = json.loads(stdout)
        return None if data.get("is_error") else data.get("result")
    text = None  # codex: one JSON event per line; keep the last agent message
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item") or {}
        msg = event.get("msg") or {}
        if item.get("type") in ("agent_message", "assistant_message"):
            text = item.get("text", text)
        elif msg.get("type") == "agent_message":
            text = msg.get("message", text)
    return text


def _parse_summary(text):
    """{"overview": ..., "files": {...}} from the AI's text, allowing ```json fences around it."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def clean(data, sent_paths):
    """Keep only files that were sent, and short lines."""
    overview = str(data.get("overview") or "").strip()[:MAX_OVERVIEW]
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    lines = {path: str(line).strip()[:MAX_LINE] for path, line in files.items() if path in sent_paths and line}
    return {"overview": overview, "files": lines}


def run(message, agent, sent_paths, root=None, command=None, timeout=TIMEOUT_SECONDS):
    """Ask `agent` for a summary. Returns (summary, None) or (None, reason).

    `command` replaces the real agent command (used by tests). The message and reply are never logged.
    """
    summary, reason = _run(message, agent, sent_paths, command, timeout)
    if root is not None:
        if summary:
            store.log(root, f"summary by {agent}: {len(summary['files'])} file(s)")
        else:
            store.log(root, f"summary by {agent or 'no agent'} unavailable: {reason}")
    return summary, reason


def _run(message, agent, sent_paths, command, timeout):
    if agent not in AGENTS:
        return None, "no agent found"
    if command is None:
        exe = shutil.which(AGENTS[agent][0])
        if not exe:
            return None, f"{agent} not found"
        command = [exe, *AGENTS[agent][1:]]

    env = dict(os.environ, **{LOOP_GUARD: "1"})
    with tempfile.TemporaryDirectory(prefix="lineup-") as empty:
        try:
            result = subprocess.run(command, input=message.encode("utf-8"), cwd=empty, env=env,
                                    capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, f"{agent} timed out"
        except OSError:
            return None, f"{agent} could not start"
    if result.returncode != 0:
        return None, f"{agent} failed (exit {result.returncode})"

    try:
        text = _reply_text(agent, result.stdout.decode("utf-8", "replace"))
        if not text:
            return None, f"{agent} failed"
        return clean(_parse_summary(text), set(sent_paths)), None
    except ValueError:
        return None, "reply wasn't readable"
