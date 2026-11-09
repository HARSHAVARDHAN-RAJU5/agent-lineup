"""Command-line entry point: `lineup <command>`."""

import argparse
import os
import sys
import textwrap
import time

from lineup import (__version__, banner, changes, export, git, guard, hooks, llm, rollback, rules, snapshot, store,
                    summary, usage)
from lineup import table as tables


def _root():
    try:
        return git.repo_root()
    except git.GitError as e:
        print(f"lineup: {e}", file=sys.stderr)
        return None


def _commit_label(commit):
    return "no commits yet" if commit == git.EMPTY_TREE else commit[:7]


def _hook(args):
    if args.event != "start" or (args.agent and not args.fresh):  # called by Claude Code / Codex
        return hooks.run_hook(args.event, args.agent)
    root = _root()
    if root is None:
        return 2
    table, kept = snapshot.start_session(root, fresh=args.fresh, agent=args.agent)
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


def scores(root, found):
    """{path: "HIGH" | "MED" | "LOW"} from the rules (the same scores `risky` shows)."""
    return {c.path: rules.score(rules.rules_for(c, found, root)) for c in found}


def _ai_summary(root, table, found):
    """The AI part of `diff`: Summary text plus one line per file. Never stops `diff` from printing."""
    levels = scores(root, found)
    items, skipped = guard.ai_input(found, levels)
    agent = llm.pick_agent(table)
    if agent is None:
        return "Summary unavailable: no agent found.\n\n"
    is_installed = bool(llm.installed(llm.AGENTS[agent][0]))

    def say_asking():
        if is_installed:  # no "Asking" line for an agent that can't be asked
            print(f"Asking {agent} for a summary...", file=sys.stderr, flush=True)

    result = summary.summarize(root, items, agent, on_ask=say_asking)
    notes = {path: note for path, _, note, _ in items if note}
    ids = {p: r["id"] for p, r in (table or {}).get("files", {}).items() if "id" in r}
    cut = [path for path, _, _, diff in items if diff.endswith(guard.CUT_MARK)]
    rows = []
    for path in sorted(levels, key=lambda p: (-rules.LEVELS[levels[p]], p)):
        if result["files"].get(path):
            line = result["files"][path] + (" (diff cut)" if path in cut else "")
        elif path in skipped:
            line = f"not summarized ({levels[path]}, diff too big)"
        elif path in notes:
            line = notes[path][:1].upper() + notes[path][1:]
        else:
            line = "no summary yet"
        rows.append((ids.get(path, "-"), path, levels[path], line))

    out = []
    if result["overview"]:
        text = textwrap.fill(result["overview"], 78, initial_indent="  ", subsequent_indent="  ")
        out.append(f"Summary (AI guess, by {agent})\n{text}\n")
    else:
        out.append(f"Summary unavailable: {result['reason'] or 'no answer'}.\n")
    out.append(tables.draw(("ID", "File", "Risk", "What changed (AI guess)"), [rows], (4, 40, 4, 50)).rstrip("\n"))
    limits = []
    if cut:
        limits.append(f"Not fully summarized: {', '.join(cut)} (diff cut, too long).")
    if skipped:
        limits.append(f"Not sent: {', '.join(skipped)} (over the {guard.AI_LINE_LIMIT:,}-line limit).")
    if limits:
        out.append(" ".join(limits))
    return "\n".join(out) + "\n\n"


def _diff(args):
    root = _root()
    if root is None:
        return 2
    table, found, committed, label = _collect(root)
    usage.record(root, "command", name="diff")
    if committed:
        names = ", ".join(f"{c.path} ({c.file_kind})" for c in committed)
        print(f"Committed since last check: {names}\n")
    if found:
        try:
            ai = _ai_summary(root, table, found)
        except Exception:  # the AI part must never stop diff from printing
            ai = "Summary unavailable: unexpected error.\n\n"
        ids = {p: r["id"] for p, r in (table or {}).get("files", {}).items() if "id" in r}
        groups = changes.format_groups(changes.sort_into_groups(found), label, ids=ids)
        title, _, body = groups.partition("\n\n")
        print(f"{title}\n\n{ai}{body}", end="")
    else:
        print(_no_changes("diff", root, table))
    _print_staged(root, found)
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
    usage.record(root, "command", name="risky")
    committed = committed_rows(table)
    if not found and not committed:
        print(_no_changes("risky", root, table))
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
    _print_staged(root, found or committed)
    if table is None:
        _no_photo(root)
    return 0


def _no_changes(command, root, table):
    since = "photo" if table else "last commit"
    if git.staged_differs(root):  # never say "no changes" while a staged copy still differs
        return f"lineup {command}: no changes in your files since the {since}, but a staged copy still has a change:"
    return f"lineup {command}: no changes since the {since}"


def _print_staged(root, shown_something):
    lines = staged_note(root)
    if lines:
        print(("\n" if shown_something else "") + "\n".join(lines))


def staged_note(root, only=None):
    """Warning lines for files whose staged copy still differs from the file on disk."""
    staged = sorted(p for p in git.staged_differs(root) if only is None or p in only)
    return [f"The staged copy of {p} still has the old change. Run git restore --staged {p} before committing."
            for p in staged]


def _names_skipped(root, token, leave_out):
    """True if what the user typed names a file lineup skips (a cache file or one git ignores)."""
    text = token.strip().replace("\\", "/").removeprefix("./")
    if text.isdigit():
        return False
    if any(p == text or p.endswith("/" + text) for p in leave_out):
        return True
    return text in changes.skipped(root, [text])


def _rollback(args):
    root = _root()
    if root is None:
        return 2
    usage.record(root, "command", name="rollback")
    if not store.has_snapshot(root):
        print("No snapshot from the session start, so rollback could wipe your own work. Nothing was changed.")
        return 1
    last_check = store.load_table(root)  # before refreshing: to spot files changed since the last check
    table, _, _, _ = _collect(root)  # new commits first, so committed files are refused
    leave_out = changes.skipped(root, table["files"])  # e.g. an older photo's __pycache__ rows
    visible = {**table, "files": {p: r for p, r in table["files"].items() if p not in leave_out}}
    resolved, problems_found = [], []
    for token in args.ids:  # IDs or file names
        file_id, problem = rollback.resolve(visible, token)
        if problem and _names_skipped(root, token, leave_out):
            problem = (f"{token} is a cache file or a file git ignores. lineup skips those, "
                       "so there's nothing to roll back")
        if problem:
            problems_found.append(problem)
        else:
            resolved.append(file_id)
    ids = list(dict.fromkeys(resolved))  # "3 3" (or "3 auth.py") counts once
    unknown = [i for i in ids if i not in rollback.valid_ids(visible)]
    if problems_found or unknown:  # a typo or an unclear name: change nothing
        valid = ", ".join(str(i) for i in rollback.valid_ids(visible)) or "none yet (run lineup risky first)"
        lines = [f"{problem[:1].upper()}{problem[1:]}." for problem in problems_found]
        if unknown:
            lines.append(f"No file with ID {', '.join(str(i) for i in unknown)}.")
        print(" ".join(lines) + f" Valid IDs: {valid}. Nothing was changed.")
        return 1

    undos = [rollback.plan_undo(root, table, i, last_check) for i in ids]
    run = rollback.new_backup_run(root)
    width = max(len(u.path) for u in undos)
    print("lineup rollback\n")
    problems = False
    for undo in undos:
        word = rollback.apply_undo(root, undo, run)
        if undo.action == "refuse":
            problems = True
            word = f"skipped: {undo.reason}"
        elif undo.action == "nothing":
            word = f"nothing to do: {undo.reason}"
        elif word.startswith("failed"):
            problems = True
        outcome = ("refused" if undo.action == "refuse" else "nothing_to_do" if undo.action == "nothing"
                   else "failed" if word.startswith("failed") else "done")
        flagged_at = table["files"].get(undo.path, {}).get("flagged_at")
        usage.record(root, "rollback", outcome=outcome, type=usage.file_type(undo.path),
                     seconds_since_flag=int(time.time()) - flagged_at if isinstance(flagged_at, int) else None)
        print(f"{undo.id:<3} {undo.path:<{width}}  {word}")
        if undo.action in rollback.DONE and not word.startswith("failed"):
            for warning in undo.warnings:
                print(f"{'':<3} {'':<{width}}  Warning: {warning}")
    done = [u.path for u in undos if u.action in rollback.DONE]
    if done:
        store.add_rollback_note(root, done)  # the agent is told on its next turn (phase 7 hooks)
    if store.backup_run_exists(root, run):
        print(f"\nYour versions from before this rollback are saved in .git/lineup/backups/{run}/.")
    staged = staged_note(root, set(done))
    if staged:
        print("\n" + "\n".join(staged))
    if done:
        notes = rollback.other_files_notes(root, table, done, set(ids))
        if notes:
            print("\n" + "\n".join(notes))
        if hooks.installed(root):
            print("\nThe agent will be told on your next message that these files were rolled back.")
    return 1 if problems else 0


def _report(args):
    root = _root()
    if root is None:
        return 2
    if args.export:
        usage.record(root, "command", name="report")
        path = export.write(root)
        print(f"Wrote {path}")
        print(f"\n{export.READ_FIRST}")
        print("It's kept out of git, and lineup itself skips it. Nothing has been sent.")
        return 0
    print(export.report_text(usage.read(root)))
    usage.record(root, "command", name="report")
    return 0


NOTE_USAGE = 'Usage: lineup note "what happened"   (lineup note --list shows your saved notes)'


def _note(args):
    root = _root()
    if root is None:
        return 2
    usage.record(root, "command", name="note")
    if args.list:
        notes = store.load_notes(root)
        if not notes:
            print("No notes yet. Add one with: lineup note \"what happened\"")
        for stamp, text in notes:
            print(f"{stamp[:16].replace('T', ' ')}  {text}")
        return 0
    text = " ".join(args.text).strip()
    if not text:
        print(NOTE_USAGE)
        return 2
    cleaned, hidden = guard.redact(text)
    store.save_note(root, cleaned)
    print("Note saved.")
    if hidden:
        word = "possible secret was" if hidden == 1 else "possible secrets were"
        print(f"{hidden} {word} replaced with {guard.REDACTED} before saving.")
    return 0


def _confirm(args, question, flag):
    """True to go ahead: --yes, or the user says yes. Never asks when there's no one to answer."""
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print(f"\nNothing was written. Run lineup init {flag}--yes to do it.")
        return False
    try:
        answer = input(f"\n{question} [y/N] ")
    except EOFError:  # no one to answer after all (e.g. NUL on Windows looks like a terminal)
        print(f"\nNothing was written. Run lineup init {flag}--yes to do it.")
        return False
    if answer.strip().lower() in ("y", "yes"):
        return True
    print("Nothing was written.")
    return False


def _init(args):
    banner.show()
    root = _root()
    if root is None:
        return 2
    if args.remove:
        return _init_remove(root, args)
    agents = ["claude", "codex"] if args.agent == "both" else [args.agent]
    plan = hooks.plan_init(root, agents)
    if not plan:
        print("lineup hooks are already installed. Nothing to change.")
        for warning in hooks.tracked_warnings(root):
            print(warning)
        return 0
    print("lineup init will write these hooks (your other settings stay as they are):\n")
    for rel, (_, added, replaced) in plan.items():
        print(f"  {rel}")
        for event, command in added:
            print(f"    {event:<17} {command}")
        if replaced:
            print("    replacing lineup's older hooks:")
            for event, command in replaced:
                print(f"      {event:<17} {command}")
    if any(event == "SessionStart" for _, added, _ in plan.values() for event, _ in added):
        note = textwrap.fill(hooks.SESSION_NOTE, 76, initial_indent="    ", subsequent_indent="    ")
        print(f"\nAt each session start, the agent is also given this note:\n{note}")
    if not _confirm(args, "Write these changes?", ""):
        return 1
    hooks.write_plan(root, plan)
    usage.record(root, "init", agents=agents, **usage.environment())
    excluded = [rel for rel in plan if hooks.keep_out_of_git(root, rel)]
    print("\nDone.")
    if excluded:
        print(f"Kept out of git (added to .git/info/exclude): {', '.join(excluded)}")
    for warning in hooks.tracked_warnings(root):
        print(warning)
    if any(rel.startswith(".claude") for rel in plan):
        print("Claude Code: the hooks load in your next session (or run /hooks to check them).")
    if any(rel.startswith(".codex") for rel in plan):
        print("Codex: open Codex in this repo and run /hooks to review and trust the lineup hooks. "
              "They don't run until you trust them.")
        if os.name == "nt":
            print("Codex on Windows: if you trusted lineup's hooks before, run /hooks again. "
                  "Codex needs you to re-trust hooks that changed.")
    return 0


def _init_remove(root, args):
    plan = hooks.plan_remove(root)
    if not plan:
        print("No lineup hooks found. Nothing to remove.")
        return 0
    print("lineup init --remove will take out only lineup's hooks (everything else stays):\n")
    for rel, (data, removed) in plan.items():
        print(f"  {rel}{'  (only lineup was in it, so the file is deleted)' if data is None else ''}")
        for event, command in removed:
            print(f"    {event:<17} {command}")
    if not _confirm(args, "Remove them?", "--remove "):
        return 1
    hooks.write_plan(root, plan)
    usage.record(root, "init_remove", **usage.environment())
    print("\nDone. lineup's hooks are removed, so the agent no longer gets lineup's session-start note either.")
    _after_remove(root, args)
    return 0


def _ask(question, default):
    """Ask a yes/no question. Enter gives `default`; None if no one can answer (piped, or no input)."""
    try:
        answer = input(f"\n{question} {'[Y/n]' if default else '[y/N]'} ").strip().lower()
    except EOFError:
        return None
    return default if not answer else answer in ("y", "yes")


def _after_remove(root, args):
    """Offer the export first, then deleting lineup's data. Never asks with --yes or piped input: both no."""
    drawer = git.git_dir(root) / "lineup"
    interactive = not args.yes and sys.stdin.isatty()
    if interactive and usage.has_data(root):
        if _ask("Export your lineup notes first?", default=True):
            path = export.write(root)
            print(f"Wrote {path}\n{export.READ_FIRST}")
    if not drawer.exists():
        return
    if interactive and _ask("Also delete lineup's saved data (photo, copies, backups)?", default=False):
        store.delete_all(root)  # after the export, never before
        print("Deleted .git/lineup/.")
        return
    print("lineup's saved data stays in .git/lineup/. To send your notes, run lineup report --export.")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="lineup",
        description="See what your coding agent just changed, which edits are risky, and undo a file.",
    )
    parser.add_argument("--version", action="store_true", help="show the version and exit")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="install the session hooks in this repo")
    init.add_argument("--agent", choices=["both", *llm.AGENTS], default="both", help="which agent's hooks (default: both)")
    init.add_argument("--yes", action="store_true", help="write without asking")
    init.add_argument("--remove", action="store_true", help="remove lineup's hooks (and nothing else)")
    init.set_defaults(func=_init)
    sub.add_parser("diff", help="show what changed since the snapshot").set_defaults(func=_diff)
    sub.add_parser("risky", help="score each change HIGH / MED / LOW").set_defaults(func=_risky)

    undo = sub.add_parser("rollback", help="undo one or more files by ID")
    undo.add_argument("ids", nargs="+", metavar="id_or_file",
                      help="file IDs or names from lineup risky / diff, e.g. 3 or auth.py")
    undo.set_defaults(func=_rollback)
    report = sub.add_parser("report", help="usage counts for testers to send (nothing is sent by lineup)")
    report.add_argument("--export", action="store_true",
                        help=f"write {export.FILE} in the repo root: summary, usage tables and your notes")
    report.set_defaults(func=_report)

    note = sub.add_parser("note", help="save a short tester note for this repo (secrets are hidden)")
    note.add_argument("text", nargs="*", help='what happened, e.g. "rollback put back the wrong version"')
    note.add_argument("--list", action="store_true", help="show the saved notes")
    note.set_defaults(func=_note)

    hook = sub.add_parser("hook", help="run by the agent's hooks (start: photo, stop: score line, prompt: notes)")
    hook.add_argument("event", choices=["start", "stop", "prompt"])
    hook.add_argument("--fresh", action="store_true", help="always take a new photo")
    hook.add_argument("--agent", choices=list(llm.AGENTS), help="the agent running this session")
    hook.set_defaults(func=_hook)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version or args.command is None:
        banner.show()
        if args.version:
            print(f"lineup {__version__}")
        else:
            parser.print_help()
        return 0
    try:
        return args.func(args)
    except (OSError, ValueError, git.GitError) as error:  # one clear line, never a Python traceback
        print(f"lineup: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
