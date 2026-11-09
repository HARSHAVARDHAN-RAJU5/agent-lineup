"""Runs the user's own agent (Claude Code or Codex) to summarize changes. Never crashes lineup."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time

from lineup import store, usage

# One line per agent. A new agent = one new line here.
AGENTS = {
    "claude": ["claude", "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
               "--strict-mcp-config", "--model", "haiku"],
    # No -m for codex: a ChatGPT login rejects gpt-5.4-mini, so codex uses its own default model.
    "codex": ["codex", "exec", "--json", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check", "-"],
}
LOOP_GUARD = "LINEUP_RUNNING"  # hooks exit when they see this, so the AI call can't trigger them again
TIMEOUT_SECONDS = 120
MAX_LINE = 200
MAX_OVERVIEW = 600


def installed(command):
    """Full path of an agent's command, or None. (Tests replace this so no real AI is ever called.)"""
    return shutil.which(command)


def pick_agent(table, which=None):
    """The agent saved in the photo, else the first one installed. None if there's none."""
    which = which or installed
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


def _own_group():
    """Start the agent in its own process group, so it and everything it starts can be stopped together."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _stop_all(proc):
    """Stop the agent and every program it started. Never raises."""
    try:
        if os.name == "nt":  # /T = the whole tree; taskkill ships with Windows
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
        proc.communicate(timeout=5)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass


def _remove_quietly(folder):
    """Delete the temp folder; if something still holds it, try again briefly, then leave it. Never raises."""
    for _ in range(5):
        shutil.rmtree(folder, ignore_errors=True)
        if not os.path.exists(folder):
            return
        time.sleep(0.2)


def run(message, agent, sent_paths, root=None, command=None, timeout=None):
    """Ask `agent` for a summary. Returns (summary, None) or (None, reason).

    `command` replaces the real agent command (used by tests). The message and reply are never logged.
    """
    timer = usage.Timer()
    summary, reason, tokens = _run(message, agent, sent_paths, command, timeout)
    if root is not None:
        result = "ok" if summary else "timeout" if reason == "timed out" else "failed"
        usage.record(root, "ai_summary", agent=agent, seconds=timer.seconds(), tokens=tokens, result=result,
                     files=len(sent_paths))
        if summary:
            store.log(root, f"summary by {agent}: {len(summary['files'])} file(s)")
        else:
            store.log(root, f"summary by {agent or 'no agent'} unavailable: {reason}")
    return summary, reason


def _run(message, agent, sent_paths, command, timeout):
    if agent not in AGENTS:
        return None, "no agent found", None
    if command is None:
        exe = installed(AGENTS[agent][0])
        if not exe:
            return None, f"{agent} not found", None
        command = [exe, *AGENTS[agent][1:]]

    env = dict(os.environ, **{LOOP_GUARD: "1"})
    empty = tempfile.mkdtemp(prefix="lineup-")
    try:
        try:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, cwd=empty, env=env, **_own_group())
        except OSError:
            return None, f"{agent} could not start", None
        try:
            stdout, _ = proc.communicate(message.encode("utf-8"), timeout=timeout or TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            _stop_all(proc)
            return None, "timed out", None
    finally:
        _remove_quietly(empty)
    if proc.returncode != 0:
        return None, f"{agent} failed (exit {proc.returncode})", None

    output = stdout.decode("utf-8", "replace")
    tokens = _tokens(output)
    try:
        text = _reply_text(agent, output)
        if not text:
            return None, f"{agent} failed", tokens
        return clean(_parse_summary(text), set(sent_paths)), None, tokens
    except ValueError:
        return None, "reply wasn't readable", tokens


def _tokens(output):
    """Total tokens if the agent reports them (Claude: "usage" in its JSON; Codex: on its turn events), else None."""
    total = None
    for line in [output] + output.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        found = data.get("usage") if isinstance(data, dict) else None
        if isinstance(found, dict):
            numbers = [found.get(k) for k in ("input_tokens", "output_tokens") if isinstance(found.get(k), int)]
            if numbers:
                total = sum(numbers)
    return total
