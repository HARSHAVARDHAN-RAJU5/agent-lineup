"""Command-line entry point: `lineup <command>`."""

import argparse
import sys

from lineup import __version__


def _not_built(args):
    print(f"lineup {args.command}: not built yet")
    return 1


def build_parser():
    parser = argparse.ArgumentParser(
        prog="lineup",
        description="See what your coding agent just changed, which edits are risky, and undo a file.",
    )
    parser.add_argument("--version", action="version", version=f"lineup {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="install the session hooks in this repo").set_defaults(func=_not_built)
    sub.add_parser("diff", help="show what changed since the snapshot").set_defaults(func=_not_built)
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
