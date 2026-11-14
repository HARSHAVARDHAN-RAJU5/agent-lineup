"""Scope: a sensitive file the agent changed although your request never mentioned it. No AI, words only.

Your messages are kept as words only (secrets hidden first) in .git/lineup/, never logged, and forgotten at the
next new session. At the end of a reply that changed files, lineup takes your last message. If it's only a
go-ahead ("yes go ahead"), it steps back to the message before, and so on, but never past the mark: the
message used last time. Back at the mark, that message is used again ("continue" carries on the last task).
"""

import re

from lineup import guard, rules, store
from lineup.changes import file_kind, is_private, path_words

REASON = "outside your request (scope)"
MAX_MESSAGES = 20

# A message made only of these words is a go-ahead, not a request.
GO_AHEAD = {
    "y", "yes", "yeah", "yep", "yup", "ya", "ok", "okay", "okey", "k", "kk", "sure", "go", "ahead", "on", "continue",
    "proceed", "do", "it", "that", "this", "please", "pls", "plz", "sound", "good", "fine", "great", "perfect",
    "thank", "thanks", "thx", "you", "alright", "right", "cool", "nice", "done", "next", "keep", "going", "carry",
    "agreed", "agree", "approved", "approve", "lgtm", "ship", "now", "and", "then", "just", "let", "lets", "s",
}

# Sensitive file kinds, the words that mean you asked about them, and how the note names them.
TOPICS = {
    "auth": ({"auth", "authn", "authz", "authenticate", "authentication", "authorize", "authorization", "oauth",
              "login", "logout", "signin", "signup", "sign", "session", "password", "passwd", "token", "jwt", "sso",
              "user", "account", "permission", "role", "payment", "pay", "billing", "bill", "stripe", "checkout",
              "invoice", "subscription", "credential", "cookie"}, "auth"),
    "config": ({"config", "configuration", "configure", "setting", "env", "environment", "docker", "dockerfile",
                "compose", "deploy", "deployment", "yaml", "yml", "toml", "ini", "cfg"}, "config"),
    "ci": ({"ci", "cd", "workflow", "pipeline", "github", "action", "hook", "claude", "codex", "agent", "build"},
           "CI or agent settings"),
    "env": ({"env", "dotenv", "environment", "secret", "key", "credential", "token", "variable", "var"},
            "env or keys"),
    "schema": ({"migration", "migrate", "schema", "database", "db", "table", "column", "model", "sql", "prisma",
                "alembic", "index"}, "the database"),
    "dependencies": ({"dependency", "dep", "package", "install", "upgrade", "downgrade", "version", "requirement",
                      "npm", "pip", "yarn", "pnpm", "poetry", "lock", "lockfile", "library", "lib"}, "dependencies"),
    "install": ({"install", "setup", "postinstall", "preinstall", "script"}, "install scripts"),
}


def stem(word):
    """Plural to singular, roughly: settings -> setting, dependencies -> dependency."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words(text):
    """The words of a message, secrets hidden first."""
    clean, _ = guard.redact(text)
    clean = clean.replace(guard.REDACTED, " ")
    return {stem(w) for w in re.findall(r"[a-z0-9]+", clean.lower())}


def is_go_ahead(message_words):
    return all(w in GO_AHEAD for w in message_words)  # an empty message counts too


def remember(root, prompt):
    """Keep one of your messages (as words only) until the next check."""
    data = store.load_requests(root)
    data["messages"] = (data.get("messages", []) + [sorted(words(prompt))])[-MAX_MESSAGES:]
    store.save_requests(root, data)


def take_request(root):
    """The words of the request this reply worked on, then move the mark. None if lineup never saw a message."""
    data = store.load_requests(root)
    messages = data.get("messages", [])
    if not messages and "mark" not in data:
        return None
    request = None
    for message in reversed(messages):  # newest first, back to the mark
        if not is_go_ahead(message):
            request = message
            break
    if request is None:
        request = data.get("mark", [])  # only go-aheads since the mark: carry on the marked request
    store.save_requests(root, {"mark": request, "messages": []})
    return set(request)


def kind(change):
    """The sensitive kind of a changed file, or None for ordinary code, docs and tests."""
    path = change.path
    name = path.lower().rsplit("/", 1)[-1]
    if change.copy_of_private or is_private(path):
        return "env"
    if path.startswith(rules.AGENT_AND_CI):
        return "ci"
    if name in rules.INSTALL_SCRIPTS or name.startswith(("postinstall", "preinstall")):
        return "install"
    found = file_kind(path)
    if found == "schema":
        return "schema"
    if found in ("lockfile", "dependencies"):
        return "dependencies"
    if found in ("config", "auth"):
        return found
    return None


def check(change, request):
    """The topic name if this change is outside the request, else None."""
    sensitive = kind(change)
    if sensitive is None or request is None:
        return None
    topic_words, label = TOPICS[sensitive]
    own = {stem(w) for w in path_words(change.path)}
    return None if request & (topic_words | own) else label


def note(path, label):
    return f"scope: {path} changed, but your request didn't mention {label}"


def flags(table, path):
    """The extra flag for a file with a scope hit that wasn't dismissed: [("HIGH", REASON)] or []."""
    hit = ((table or {}).get("files", {}).get(path) or {}).get("scope")
    return [("HIGH", REASON)] if isinstance(hit, dict) and not hit.get("dismissed") else []


def all_flags(change, found, root, table):
    """The rule flags (highest first), then the scope and claims flags if there are any."""
    from lineup import claims  # here, to keep imports one-way (claims imports scope)
    ruled = sorted(rules.rules_for(change, found, root), key=lambda f: -rules.LEVELS[f[0]])
    return ruled + flags(table, change.path) + claims.flags(table, change.path)


def update(root, table, this_reply):
    """Check the files changed in this reply against your request. Returns [(path, label)] for new hits.

    A file changed again is checked fresh, even if its earlier hit was dismissed. Saves the table.
    """
    request = take_request(root)
    new = []
    for change in this_reply:
        row = table["files"].get(change.path)
        if row is None:
            continue
        label = check(change, request)
        if label:
            row["scope"] = {"label": label}
            new.append((change.path, label))
        else:
            row.pop("scope", None)
    store.save_table(root, table)
    return new


def hits(table, paths):
    """[(id, path, label)] for undismissed hits among `paths`, by ID."""
    rows = (table or {}).get("files", {})
    out = [(rows[p].get("id", "-"), p, rows[p]["scope"].get("label", "?")) for p in paths
           if isinstance((rows.get(p) or {}).get("scope"), dict) and not rows[p]["scope"].get("dismissed")]
    return sorted(out, key=lambda h: (h[0] if isinstance(h[0], int) else 0, h[1]))
