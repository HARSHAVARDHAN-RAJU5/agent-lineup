"""lineup report and lineup report --export: the usage log and notes, readable, for testers to send.

Built only from the usage log (types, counts, times) and the tester's notes (secrets already hidden).
Never from audit.log, the table or the copies, which hold file names and code.
"""

from datetime import datetime
from pathlib import Path

from lineup import __version__, guard, store, usage

FILE = "lineup-notes.md"
READ_FIRST = "Read this before sending. It should contain no file names, code or secrets. If you see any, delete them."


def report_rows(s):
    """The summary rows shared by lineup report and the export: counts, dates and types only."""
    days = s["days"]
    period = f" ({days[0]} to {days[-1]})" if days else ""
    r, f, cmd = s["rollbacks"], s["flags"], s["commands"]
    per_reply = s["files_per_reply"]
    seconds = s["ai_seconds"]
    ai = f"{s['ai_runs']} runs"
    if seconds:
        ai += f", avg {sum(seconds) / len(seconds):.1f}s"
    ai += f", failed {s['ai_failed']}, timed out {s['ai_timeouts']}"
    if s["ai_tokens"]:
        ai += f", tokens {sum(s['ai_tokens']):,} (when reported)"
    return [
        ("lineup version", s["lineup"] or __version__),
        ("Days active", f"{len(days)}{period}"),
        ("Agents", ", ".join(s["agents"]) or "none yet"),
        ("OS / Python", f"{s['os'] or '?'} / {s['python'] or '?'}"),
        ("Sessions started", s["sessions"]),
        ("Agent replies with changes", s["replies"]),
        ("Files changed per reply", f"avg {sum(per_reply) / len(per_reply):.1f}, max {max(per_reply)}"
         if per_reply else "-"),
        ("Files flagged after replies", f"HIGH {f['HIGH']}, MED {f['MED']}, LOW {f['LOW']}"),
        ("lineup diff runs", cmd.get("diff", 0)),
        ("lineup risky runs", cmd.get("risky", 0)),
        ("Rollbacks", f"done {r.get('done', 0)}, refused {r.get('refused', 0)}, "
                      f"failed {r.get('failed', 0)}, nothing to do {r.get('nothing_to_do', 0)}"),
        ("AI summaries", ai),
        ("Hook errors", s["hook_errors"]),
    ]


def report_text(events):
    """The usage summary printed by lineup report."""
    if not events:
        return "lineup report: no usage recorded in this repo yet."
    rows = report_rows(usage.summarize(events))
    width = max(len(name) for name, _ in rows)
    lines = ["lineup report (this repo only)", ""]
    lines += [f"{name:<{width}}  {value}" for name, value in rows]
    lines += ["", "That's everything in the report: counts and dates only, no code, file names or secrets.",
              "Nothing has been sent. To share it, copy the lines above, or run lineup report --export."]
    return "\n".join(lines)


# ---- the export file ----

def _cell(value):
    """A log value made safe for a table: numbers and type-like text only (anything else shows as ?)."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, str) and len(value) <= 40 and usage.SAFE_TEXT.match(value):
        return value
    if isinstance(value, list):
        return ", ".join(_cell(v) for v in value)
    return "?"


def _when(event):
    stamp = event.get("t")
    return stamp[:16].replace("T", " ") if isinstance(stamp, str) else "?"


def _duration(seconds):
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds < 0:
        return "-"
    if seconds < 120:
        return f"{seconds:.0f}s"
    if seconds < 7200:
        return f"{seconds / 60:.0f} min"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} days"


def _table(header, rows):
    if not rows:
        return "_None._\n"
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def _of(events, name):
    return [e for e in events if e.get("event") == name]


def build(events, notes):
    """The text of lineup-notes.md."""
    s = usage.summarize(events)
    out = [f"# lineup notes\n", f"> **{READ_FIRST}**\n",
           f"Exported {datetime.now().strftime('%Y-%m-%d %H:%M')} by lineup {__version__}. This repo only. "
           "Made from lineup's usage log (file types, counts and times) and your notes.\n"]

    out.append("## Summary\n")
    out.append(_table(("Item", "Value"), [(name, value) for name, value in report_rows(s)]))

    fired = {}
    for reply in _of(events, "reply"):
        for rule in reply.get("rules") or []:
            if isinstance(rule, dict):
                key = (_cell(rule.get("rule")), _cell(rule.get("risk")), _cell(rule.get("type")))
                fired[key] = fired.get(key, 0) + 1
    order = {"HIGH": 0, "MED": 1, "LOW": 2}
    out.append("## Rules fired after agent replies\n")
    out.append(_table(("Rule", "Risk", "File type", "Times"),
                      [(*key, n) for key, n in sorted(fired.items(), key=lambda kv: (order.get(kv[0][1], 3), -kv[1]))]))

    out.append("## Agent replies with changes\n")
    out.append(_table(("When", "Agent", "Files changed", "HIGH", "MED", "LOW"),
                      [(_when(e), _cell(e.get("agent")), _cell(e.get("files_changed")),
                        *(_cell((e.get("flags") or {}).get(level, 0)) for level in ("HIGH", "MED", "LOW")))
                       for e in _of(events, "reply")]))

    out.append("## Rollbacks\n")
    out.append(_table(("When", "File type", "Outcome", "Time after the flag"),
                      [(_when(e), _cell(e.get("type")), _cell(e.get("outcome")), _duration(e.get("seconds_since_flag")))
                       for e in _of(events, "rollback")]))

    out.append("## AI summaries (lineup diff)\n")
    out.append(_table(("When", "Agent", "Seconds", "Tokens", "Files", "Result"),
                      [(_when(e), _cell(e.get("agent")), _cell(e.get("seconds")), _cell(e.get("tokens")),
                        _cell(e.get("files")), _cell(e.get("result"))) for e in _of(events, "ai_summary")]))

    out.append("## Hook errors\n")
    out.append(_table(("When", "Hook", "Agent", "Error type"),
                      [(_when(e), _cell(e.get("hook")), _cell(e.get("agent")), _cell(e.get("error")))
                       for e in _of(events, "hook_error")]))

    out.append("## Sessions and setup\n")
    out.append(_table(("When", "Event", "Agent", "OS", "Python", "lineup"),
                      [(_when(e), e["event"].replace("_", " "), _cell(e.get("agent") or e.get("agents")),
                        _cell(e.get("os")), _cell(e.get("python")), _cell(e.get("lineup")))
                       for e in events if e.get("event") in ("session_start", "init", "init_remove")]))

    out.append("## Commands run\n")
    out.append(_table(("Command", "Runs"), [(f"lineup {_cell(name)}", n) for name, n in sorted(s["commands"].items())]))

    out.append("## Your notes\n")
    if notes:
        for stamp, text in notes:
            clean, _ = guard.redact(text)  # hidden when saved; checked again in case of older notes
            clean = clean.replace("\r", "").replace("\n", "\n  ")
            out.append(f"- {stamp[:16].replace('T', ' ')}: {clean}")
        out.append("")
    else:
        out.append('_No notes. Add one any time with lineup note "what happened"._\n')
    return "\n".join(out)


def write(root):
    """Write lineup-notes.md in the repo root and keep it out of git. Returns its path."""
    from lineup import hooks  # here, to keep imports one-way
    path = Path(root) / FILE
    tmp = path.with_name(FILE + ".tmp")
    tmp.write_text(build(usage.read(root), store.load_notes(root)), encoding="utf-8")
    tmp.replace(path)
    hooks.keep_out_of_git(root, FILE)
    return path
