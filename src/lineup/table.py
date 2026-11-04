"""Draws boxed tables for the terminal. Falls back to plain +-| lines if the terminal can't show boxes."""

import sys
import textwrap

BOX = {"tl": "┌", "tm": "┬", "tr": "┐", "ml": "├", "mm": "┼", "mr": "┤",
       "bl": "└", "bm": "┴", "br": "┘", "h": "─", "v": "│"}
PLAIN = {key: "+" for key in BOX} | {"h": "-", "v": "|"}


def style(stream=None):
    """Box lines if `stream` (default: the terminal) can print them, else plain ones."""
    stream = stream or sys.stdout
    try:
        "".join(BOX.values()).encode(getattr(stream, "encoding", None) or "ascii")
        return BOX
    except (UnicodeEncodeError, LookupError):
        return PLAIN


def _cell_lines(text, width):
    """Split a cell into lines no wider than `width`. Each "\n" part starts a new line;
    wrapped parts are indented by two spaces so they read as one item."""
    lines = []
    for part in str(text).split("\n"):
        lines += textwrap.wrap(part, width, subsequent_indent="  ", break_on_hyphens=False) or [""]
    return lines


def draw(headers, blocks, max_widths, box=None):
    """A table as text. `blocks` is a list of row lists; a line is drawn between blocks."""
    box = box or style()
    wrapped = [[[_cell_lines(cell, w) for cell, w in zip(row, max_widths)] for row in block] for block in blocks]
    widths = [len(h) for h in headers]
    for block in wrapped:
        for row in block:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], *(len(line) for line in cell))

    def rule(left, mid, right):
        return left + mid.join(box["h"] * (w + 2) for w in widths) + right

    def line(cells):
        return box["v"] + box["v"].join(f" {c:<{w}} " for c, w in zip(cells, widths)) + box["v"]

    out = [rule(box["tl"], box["tm"], box["tr"]), line(headers), rule(box["ml"], box["mm"], box["mr"])]
    for b, block in enumerate(wrapped):
        if b:
            out.append(rule(box["ml"], box["mm"], box["mr"]))
        for row in block:
            for i in range(max(len(cell) for cell in row)):
                out.append(line([cell[i] if i < len(cell) else "" for cell in row]))
    out.append(rule(box["bl"], box["bm"], box["br"]))
    return "\n".join(out) + "\n"
