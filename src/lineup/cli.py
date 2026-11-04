"""Command-line entry point: `lineup <command>`."""

import argparse
import sys

from lineup import __version__, changes, git, snapshot, store


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


def _diff(args):
    root = _root()
    if root is None:
        return 2
    if store.has_snapshot(root):
        table = store.load_table(root)
        flagged = snapshot.check_commits(root, table)
        if flagged:
            names = ", ".join(f"{path} ({kind})" for path, kind in flagged)
            print(f"Committed since last check: {names}\n")
        found = changes.find_changes(root, table)
        snapshot.record_check(root, table, found)
        label = f"photo taken {table['taken_at']} at {_commit_label(table['start_commit'])}"
    else:
        table = None
        found = changes.find_changes(root)
        head = git.head_commit(root)
        label = f"last commit {head[:7]}" if head else "an empty repo (no commits yet)"

    if found:
        print(changes.format_groups(changes.sort_into_groups(found), label), end="")
    else:
        print(f"lineup diff: no changes since the {'photo' if table else 'last commit'}")

    if table is None:  # no photo yet: take one now so the next check is exact
        snapshot.take(root)
        print("\nNo snapshot from the session start. These changes may include your own edits, "
              "and they can't be rolled back. Tracking from now on.")
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
    sub.add_parser("risky", help="score each change HIGH / MED / LOW").set_defaults(func=_not_built)

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
