"""Claims: what the agent said it changed vs what really changed. No AI, file names only.

At the end of a reply that changed files, lineup reads the agent's last message (in memory only, secrets
hidden, never saved or logged) and checks two things:
1. A sensitive file changed, but the message never names it: the file goes straight to HIGH with a note.
2. The message says it changed a file ("updated utils.py"), the file exists, but it didn't change: a note only.
No message (Codex can send none): nothing is checked.
"""

import re

from lineup import git, guard, store

REASON = "not mentioned by the agent (claims)"

# A sentence counts as "I changed X" only with one of these words, and none of NOT_WORDS.
CHANGE_WORDS = re.compile(
    r"\b(updat|chang|edit|modif|fix|add|creat|delet|remov|renam|rewr|wrote|writ|refactor|mov|replac|implement"
    r"|patch|tweak|adjust|bump|set)\w*", re.I)
NOT_WORDS = re.compile(r"\b(not|no|never|without|unchanged|untouched|didn't|didnt|don't|dont|won't|wont"
                       r"|can't|cant|nothing|instead of|should|could|would|might|may|suggest|recommend)\b", re.I)
URL = re.compile(r"\S+://\S+")
FILE_NAME = re.compile(r"(?<![\w./-])((?:\./)?(?:[\w.-]+/)*[\w.-]*\w\.[A-Za-z0-9]{1,8})(?![\w/-]|\.\w)")


def clean(message):
    """The agent's message with secrets and URLs removed, or None if there's no message."""
    if not isinstance(message, str) or not message.strip():
        return None
    text, _ = guard.redact(message)
    return URL.sub(" ", text.replace("\\", "/"))


def mentioned(path, text):
    """True if the message names this file, by its path or its file name (any case)."""
    lower = text.lower()
    name = path.lower().rsplit("/", 1)[-1]  # the full path ends with the name, so this covers both
    return re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-]|\.\w)", lower) is not None


def unmentioned(this_reply, text):
    """[(path, label)] for sensitive files changed in this reply that the message never names."""
    from lineup import scope  # here, to keep imports one-way (scope imports claims)
    out = []
    for change in this_reply:
        sensitive = scope.kind(change)
        if sensitive and not mentioned(change.path, text):
            out.append((change.path, scope.TOPICS[sensitive][1]))
    return out


def claimed(text):
    """File names the message says were changed: from sentences with a change word and no "not"."""
    names = []
    for sentence in re.split(r"(?<=[!?])\s+|\.\s+|\n+", text):
        if not CHANGE_WORDS.search(sentence) or NOT_WORDS.search(sentence):
            continue
        for found in FILE_NAME.findall(sentence):
            name = found.removeprefix("./")
            if name and name not in names:
                names.append(name)
    return names


def said_but_unchanged(root, text, changed):
    """File names the agent says it changed that exist in the repo but didn't change this session.

    Checked against git's file list only: no file is opened or followed.
    """
    names = claimed(text)
    if not names:
        return []
    files = {p.lower() for p in git.tracked_files(root) + git.untracked_files(root)}
    base_names = {p.rsplit("/", 1)[-1] for p in files}
    changed = {p.lower() for p in changed}
    changed_names = {p.rsplit("/", 1)[-1] for p in changed}
    out = []
    for name in names:
        lower = name.lower()
        if "/" in lower:
            exists, did_change = lower in files, lower in changed
        else:
            exists, did_change = lower in base_names, lower in changed_names
        if exists and not did_change:
            out.append(name)
    return out


def note_unmentioned(path):
    return f"claims: {path} changed, but the agent didn't mention it"


def note_unchanged(name):
    return f"claims: the agent said it changed {name}, but it didn't"


def flags(table, path):
    """The extra flag for a file with a claims hit that wasn't dismissed: [("HIGH", REASON)] or []."""
    hit = ((table or {}).get("files", {}).get(path) or {}).get("claims")
    return [("HIGH", REASON)] if isinstance(hit, dict) and not hit.get("dismissed") else []


def update(root, table, this_reply, message, changed):
    """Check this reply against the agent's message. Returns (new hits [(path, label)], unchanged names).

    `changed` is every file changed this session. A file changed again is checked fresh. With no message,
    nothing is checked and old results for these files are dropped. Saves the table.
    """
    text = clean(message)
    hits = unmentioned(this_reply, text) if text else []
    unchanged = said_but_unchanged(root, text, changed) if text else []
    hit_paths = dict(hits)
    for change in this_reply:
        row = table["files"].get(change.path)
        if row is None:
            continue
        if change.path in hit_paths:
            row["claims"] = {"label": hit_paths[change.path]}
        else:
            row.pop("claims", None)
    table["claims_unchanged"] = unchanged
    store.save_table(root, table)
    return [h for h in hits if h[0] in table["files"]], unchanged


def hits(table, paths):
    """[(id, path)] for undismissed hits among `paths`, by ID."""
    rows = (table or {}).get("files", {})
    out = [(rows[p].get("id", "-"), p) for p in paths
           if isinstance((rows.get(p) or {}).get("claims"), dict) and not rows[p]["claims"].get("dismissed")]
    return sorted(out, key=lambda h: (h[0] if isinstance(h[0], int) else 0, h[1]))


def unchanged_names(table):
    names = (table or {}).get("claims_unchanged")
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []
