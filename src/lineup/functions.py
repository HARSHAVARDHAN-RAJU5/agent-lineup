"""Finds functions and dependencies in file contents, so changes can name them one by one."""

import json
import posixpath
import re
from collections import Counter

_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "with", "else"}

_PATTERNS = {
    "python": [re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", re.M)],
    "js": [
        re.compile(r"\bfunction\s*\*?\s*(\w+)\s*\("),
        re.compile(r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|\w+\s*=>)"),
        re.compile(r"^\s*(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{", re.M),
    ],
    "go": [re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(", re.M)],
}

# JS test blocks: it("does x"), test.skip("does y") -> named `test "does x"`.
_JS_TEST = re.compile(r"\b(?:it|test)(?:\.\w+)?\s*\(\s*(['\"`])(.+?)\1")

_LANGUAGES = {
    ".py": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js",
    ".go": "go",
}


def language(path):
    return _LANGUAGES.get(posixpath.splitext(path)[1].lower())


def find_functions(path, text):
    """Function names defined in `text`, as a Counter (a name can appear twice)."""
    lang = language(path)
    if not lang or not text:
        return Counter()
    names = Counter()
    for pattern in _PATTERNS[lang]:
        for name in pattern.findall(text):
            if lang == "js" and name in _JS_KEYWORDS:
                continue
            names[name] += 1
    if lang == "js":
        for _, title in _JS_TEST.findall(text):
            names[f'test "{title}"'] += 1
    return names


def function_changes(path, old_text, new_text):
    """(added, removed) function names. A rename shows as one removed plus one added."""
    old, new = find_functions(path, old_text), find_functions(path, new_text)
    return sorted((new - old).elements()), sorted((old - new).elements())


# ---- dependencies ----

def _requirements(text):
    names = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[;@\s]", line, maxsplit=1)[0]
        if name:
            names.add(name.lower())
    return names


def _package_json(text):
    try:
        data = json.loads(text)
    except ValueError:
        return set()
    names = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            names.update(section)
    return names


def _toml_sections(text, wanted):
    """Keys inside TOML tables whose name passes `wanted(table_name)`."""
    names, inside = set(), False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inside = wanted(stripped.strip("[]").strip())
            continue
        if inside:
            match = re.match(r"([A-Za-z0-9_.\-]+)\s*=", stripped)
            if match and match.group(1).lower() != "python":
                names.add(match.group(1).lower())
    return names


def _pyproject(text):
    names = set()
    # PEP 621 lists: dependencies = ["requests>=2", ...] (also optional-dependencies groups)
    for block in re.findall(r"^\s*[\w-]*dependencies\s*=\s*\[(.*?)\]", text, re.M | re.S):
        for spec in re.findall(r"['\"]([^'\"]+)['\"]", block):
            names |= _requirements(spec)
    # Poetry tables: [tool.poetry.dependencies] / [tool.poetry.group.dev.dependencies]
    names |= _toml_sections(text, lambda t: t.startswith("tool.poetry") and t.endswith("dependencies"))
    return names


def _cargo(text):
    return _toml_sections(text, lambda t: t.endswith("dependencies"))


def _pipfile(text):
    return _toml_sections(text, lambda t: t in ("packages", "dev-packages"))


def _go_mod(text):
    names = set()
    for block in re.findall(r"^require\s*\((.*?)\)", text, re.M | re.S):
        names.update(re.findall(r"^\s*(\S+)\s+v", block, re.M))
    names.update(re.findall(r"^require\s+(\S+)\s+v", text, re.M))
    return names


def _dependency_parser(path):
    name = posixpath.basename(path).lower()
    if name == "package.json":
        return _package_json
    if name == "pyproject.toml":
        return _pyproject
    if name == "cargo.toml":
        return _cargo
    if name == "pipfile":
        return _pipfile
    if name == "go.mod":
        return _go_mod
    if re.fullmatch(r"requirements.*\.txt", name):
        return _requirements
    return None


def is_dependency_file(path):
    return _dependency_parser(path) is not None


def new_dependencies(path, old_text, new_text):
    """Packages listed in `new_text` but not in `old_text`, sorted."""
    parser = _dependency_parser(path)
    if not parser:
        return []
    return sorted(parser(new_text or "") - parser(old_text or ""))
