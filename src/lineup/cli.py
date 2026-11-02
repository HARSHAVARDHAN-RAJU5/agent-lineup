"""Command-line entry point: `lineup <command>`."""

import argparse
import sys

from lineup import __version__, changes, git


def _not_built(args):
    print(f"lineup {args.command}: not built yet")
    return 1


def _diff(args):
    try:
        root = git.repo_root()
    except git.GitError as e:
        print(f"lineup: {e}", file=sys.stderr)
        return 2
    head = git.head_commit(root)
    found = changes.find_changes(root)
    if not found:
        print("lineup diff: no changes since the last commit")
        return 0
    label = f"last commit {head[:7]}" if head else "an empty repo (no commits yet)"
    print(changes.format_groups(changes.sort_into_groups(found), label), end="")
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

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
