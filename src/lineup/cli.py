"""Command-line entry point: `lineup <command>`."""

import argparse
import sys

from lineup import __version__, changes, git, rules, snapshot, store
from lineup import table as tables


def _not_built(args):
    print(f"lineup {args.command}: not built yet")
    return 1


def _root():
    try:
        return git.repo_root()
    except git.GitError as e:
        print(f"lineup: {e}", file=sys.stderr)
        return None


def _commit_label(commit):
    return "no commits yet" if commit == git.EMPTY_TREE else commit[:7]


def _hook(args):
    root = _root()
    if root is None:
        return 2
    table, kept = snapshot.start_session(root, fresh=args.fresh)
    if kept:
        word = "file" if kept == 1 else "files"
        print(f"Keeping the earlier snapshot: {kept} uncommitted {word} from before this session. "
              "Some may be your own edits. Commit them, or run lineup hook start --fresh to start over.")
    else:
        print(f"lineup: photo taken at {_commit_label(table['start_commit'])} ({snapshot.summary(table)})")
    return 0


def _collect(root):
    """Shared by diff and risky: flag new commits, find changes, give IDs.

    Returns (table, found, committed, label). `committed` are files in new commits.
    """
    if store.has_snapshot(root):
        table = store.load_table(root)
        committed = snapshot.check_commits(root, table)
        found = changes.find_changes(root, table)
        snapshot.record_check(root, table, found)
        label = f"photo taken {table['taken_at']} at {_commit_label(table['start_commit'])}"
        return table, found, committed, label
    found = changes.find_changes(root)
    head = git.head_commit(root)
    return None, found, [], f"last commit {head[:7]}" if head else "an empty repo (no commits yet)"


def _no_photo(root):
    """No photo yet: take one now so the next check is exact."""
    snapshot.take(root)
    print("\nNo snapshot from the session start. These changes may include your own edits, "
          "and they can't be rolled back. Tracking from now on.")


def _diff(args):
    root = _root()
    if root is None:
        return 2
    table, found, committed, label = _collect(root)
    if committed:
        names = ", ".join(f"{c.path} ({c.file_kind})" for c in committed)
        print(f"Committed since last check: {names}\n")
    if found:
        print(changes.format_groups(changes.sort_into_groups(found), label), end="")
    else:
        print(f"lineup diff: no changes since the {'photo' if table else 'last commit'}")
    if table is None:
        _no_photo(root)
    return 0


def _other(change):
    notes = []
    if change.kind == "added":
        notes.append("New")
    if change.kind == "deleted":
        notes.append("Deleted")
    if change.private or change.big:
        notes.append("Can't roll back")
    return ", ".join(notes)


def _why(reasons):
    return "\n".join(reason if i == 0 else f"+ {reason}" for i, reason in enumerate(reasons))


def risky_rows(root, table, found):
    """[(id, path, level, why, other)], HIGH first, then by ID."""
    rows = []
    for change in found:
        flags = sorted(rules.rules_for(change, found, root), key=lambda f: -rules.LEVELS[f[0]])
        row_id = table["files"][change.path]["id"] if table else "-"
        rows.append((row_id, change.path, rules.score(flags), _why([r for _, r in flags]), _other(change)))
    rows.sort(key=lambda r: (-rules.LEVELS[r[2]], r[0] if isinstance(r[0], int) else 0, r[1]))
    return rows


def committed_rows(table):
    """Saved scores of files in commits since the photo: [(commit, path, level, why)], HIGH first."""
    saved = (table or {}).get("committed", [])
    rows = [(c["commit"][:7], c["path"], c["risk"], _why(c["reasons"])) for c in saved]
    return sorted(rows, key=lambda r: -rules.LEVELS[r[2]])  # stable: same level keeps commit order


def _risky(args):
    root = _root()
    if root is None:
        return 2
    table, found, _, label = _collect(root)
    committed = committed_rows(table)
    if not found and not committed:
        print(f"lineup risky: no changes since the {'photo' if table else 'last commit'}")
    else:
        print(f"lineup risky (compared with {label})\n")
    if found:
        rows = risky_rows(root, table, found)
        print(tables.draw(("ID", "File", "Risk", "Why (rule)", "Other"), [[r] for r in rows], (4, 40, 4, 50, 20)), end="")
    elif committed:
        print("No uncommitted changes since the photo.")
    if committed:
        print("\nCommitted (lineup can't roll these back; use git to undo them)\n")
        print(tables.draw(("Commit", "File", "Risk", "Why (rule)"), [[r] for r in committed], (7, 40, 4, 50)), end="")
    if table is None:
        _no_photo(root)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="lineup",
        description="See what your coding agent just changed, which edits are risky, and undo a file.",
    )
    parser.add_argument("--version", action="version", version=f"lineup {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="install the session hooks in this repo").set_defaults(func=_not_built)
    sub.add_parser("diff", help="show what changed since the snapshot").set_defaults(func=_diff)
    sub.add_parser("risky", help="score each change HIGH / MED / LOW").set_defaults(func=_risky)

    rollback = sub.add_parser("rollback", help="undo one or more files by ID")
    rollback.add_argument("ids", nargs="+", type=int, help="file IDs from `lineup risky`")
    rollback.set_defaults(func=_not_built)

    hook = sub.add_parser("hook", help="run by the agent's hooks (start: take the photo)")
    hook.add_argument("event", choices=["start"])
    hook.add_argument("--fresh", action="store_true", help="always take a new photo")
    hook.set_defaults(func=_hook)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
