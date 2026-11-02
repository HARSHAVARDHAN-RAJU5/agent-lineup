from lineup.functions import function_changes, new_dependencies


def test_python_function_removed_and_added():
    old = "def login():\n    pass\n\ndef refresh_token():\n    pass\n"
    new = "def login():\n    pass\n\nasync def fetch_user():\n    pass\n"
    assert function_changes("auth.py", old, new) == (["fetch_user"], ["refresh_token"])


def test_python_function_body_edit_is_not_add_or_remove():
    old = "def login(user):\n    return 1\n"
    new = "def login(user, remember=True):\n    return 2\n"
    assert function_changes("auth.py", old, new) == ([], [])


def test_rename_shows_as_removed_plus_added():
    assert function_changes("a.py", "def old_name():\n  pass\n", "def new_name():\n  pass\n") == (
        ["new_name"], ["old_name"])


def test_js_functions_and_tests():
    old = (
        "function save() {}\n"
        "const load = async () => {}\n"
        'it("expires old sessions", () => {})\n'
    )
    new = "function save() {}\n"
    added, removed = function_changes("app.test.js", old, new)
    assert added == []
    assert removed == ["load", 'test "expires old sessions"']


def test_js_control_flow_is_not_a_function():
    text = "if (x) {\n}\nfor (i of y) {\n}\n"
    assert function_changes("a.ts", "", text) == ([], [])


def test_go_functions():
    new = "func Serve() {}\nfunc (s *Server) Stop() {}\n"
    assert function_changes("main.go", "", new) == (["Serve", "Stop"], [])


def test_unknown_language_has_no_functions():
    assert function_changes("notes.txt", "", "def x():") == ([], [])


def test_requirements_names_each_new_package():
    old = "flask==3.0\n"
    new = "flask==3.1\nredis>=5  # cache\nrequests[socks]\n-r base.txt\n"
    assert new_dependencies("requirements.txt", old, new) == ["redis", "requests"]


def test_package_json_dev_dependencies():
    old = '{"dependencies": {"react": "18"}}'
    new = '{"dependencies": {"react": "18"}, "devDependencies": {"vitest": "1"}}'
    assert new_dependencies("package.json", old, new) == ["vitest"]


def test_pyproject_pep621_and_poetry():
    new = (
        '[project]\ndependencies = [\n  "httpx>=0.27",\n  "redis",\n]\n'
        '[tool.poetry.dependencies]\npython = "^3.10"\nrich = "^13"\n'
    )
    assert new_dependencies("pyproject.toml", "", new) == ["httpx", "redis", "rich"]


def test_go_mod():
    new = "module x\n\nrequire (\n\tgithub.com/a/b v1.0.0\n)\nrequire golang.org/x/c v0.1.0\n"
    assert new_dependencies("go.mod", "", new) == ["github.com/a/b", "golang.org/x/c"]


def test_broken_package_json_is_ignored():
    assert new_dependencies("package.json", "", "{not json") == []
