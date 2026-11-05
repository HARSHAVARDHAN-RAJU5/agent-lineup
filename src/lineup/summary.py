"""Asks the AI only about files that changed since the last check; reuses saved answers for the rest."""

import hashlib

from lineup import guard, llm, store


def key(item):
    """Fingerprint of exactly what the AI would see for one file. Changes if one character changes."""
    path, level, note, diff = item
    return hashlib.sha256("\n".join((path, level, note, diff)).encode("utf-8")).hexdigest()


def _overview_key(keys):
    joined = "\n".join(f"{path} {k}" for path, k in sorted(keys.items()))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def summarize(root, items, agent, command=None, on_ask=None):
    """Summary for `items` (from guard.ai_input). Returns {"overview", "files", "reason", "asked"}.

    "asked" lists the files whose diffs went to the AI this time; [] means no AI call was made.
    `on_ask` is called just before the AI is asked (e.g. to say "Asking claude...").
    """
    saved = store.load_summaries(root)
    keys = {item[0]: key(item) for item in items}
    old_files = saved.get("files", {})
    reused = {path: old_files[path]["line"] for path in keys
              if old_files.get(path, {}).get("key") == keys[path]}
    new_items = [item for item in items if item[0] not in reused]
    overview_key = _overview_key(keys)

    if not items:
        store.save_summaries(root, {"overview": {}, "files": {}})
        return {"overview": None, "files": {}, "reason": None, "asked": []}
    if not new_items and saved.get("overview", {}).get("key") == overview_key:
        return {"overview": saved["overview"]["text"], "files": reused, "reason": None, "asked": []}

    # New files go as their diffs; files already summarized go as their short saved line only.
    message_items = new_items + [(path, level, f"unchanged since the last check; earlier summary: {reused[path]}", "")
                                 for path, level, _, _ in items if path in reused]
    asked = [item[0] for item in new_items]
    if on_ask:
        on_ask()
    answer, reason = llm.run(guard.build_message(message_items), agent, asked, root=root, command=command)

    files = dict(reused)
    if answer:  # every file asked about is saved, even with no line (e.g. .env), so it isn't asked again
        files.update({path: answer["files"].get(path, "") for path in asked})
    store.save_summaries(root, {
        "overview": {"key": overview_key, "text": answer["overview"]} if answer else {},
        "files": {path: {"key": keys[path], "line": line} for path, line in files.items()},
    })
    return {"overview": answer["overview"] if answer else None, "files": files, "reason": reason, "asked": asked}
