"""Recognize lineup's own hook entries in Claude Code / Codex settings files."""

import copy
import json
import re

# Files that can hold lineup's hooks. The first one is the old location (before settings.local.json).
SETTINGS_FILES = (".claude/settings.json", ".claude/settings.local.json", ".codex/hooks.json")


# lineup's hook line, plain (`lineup hook stop --agent claude || echo {}`) or wrapped in cmd for Codex on
# Windows (`cmd /c "lineup hook stop --agent codex || echo {}"`).
LINEUP_COMMAND = re.compile(r'(?:(?i:cmd(?:\.exe)?)\s+/[cC]\s+"?)?lineup hook ')


def is_lineup_command(command):
    return isinstance(command, str) and LINEUP_COMMAND.match(command.strip()) is not None


def without_lineup(data):
    """A copy of a settings object with lineup's hook entries taken out; empty groups and events go too."""
    data = copy.deepcopy(data)
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return data
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            handlers = [h for h in group["hooks"] if not (isinstance(h, dict) and is_lineup_command(h.get("command")))]
            if handlers:
                kept_groups.append({**group, "hooks": handlers})
            elif len(handlers) == len(group["hooks"]):  # an empty group that wasn't lineup's
                kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        del data["hooks"]
    return data


def _load(text):
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def only_lineup_changed(old_text, new_text):
    """True if the only difference between two versions of a settings file is lineup's own hooks."""
    try:
        old, new = _load(old_text), _load(new_text)
    except ValueError:
        return False
    return old != new and without_lineup(old) == without_lineup(new)
