"""Fix 4: secret redaction before anything goes to the AI.

Fake keys are built from pieces at test time, so no full key-shaped string sits in the source
(that would trip secret scanners like GitHub push protection).
"""

import pytest

from lineup import rules, snapshot, store
from lineup.changes import Change, file_kind, find_changes
from lineup.guard import REDACTED, ai_input, build_message, redact

A36 = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # 36 mixed letters and digits

FAKE_KEYS = {
    "Google / Gemini": "AI" + "za" + "Sy" + A36[:33],
    "OpenAI (sk-)": "s" + "k-" + A36 + "xyz",
    "OpenAI (sk-proj-)": "s" + "k-proj-" + A36 + A36,
    "Anthropic": "s" + "k-ant-api03-" + A36 + A36,
    "GitHub (ghp_)": "gh" + "p_" + A36,
    "GitHub (github_pat_)": "github" + "_pat_" + "11ABCDEFG0" + A36,
    "AWS": "AK" + "IA" + "Z7Q2X9W4V1B8N3M6",
    "Stripe": "sk" + "_live_" + A36[:24],
    "Slack": "xo" + "xb-" + "123456789012-" + A36[:24],
    "Hugging Face": "h" + "f_" + A36[:34],
}


@pytest.mark.parametrize("platform", sorted(FAKE_KEYS))
def test_each_platform_key_is_hidden(platform):
    key = FAKE_KEYS[platform]
    text, count = redact(f"+client = connect({key!r})\n+print('ok')")
    assert key not in text and count >= 1, platform
    assert "print('ok')" in text


def test_whole_private_key_block_is_removed():
    block = "+-----BEGIN RSA PRIVATE KEY-----\n+MIIEowIBAAKCAQEAline1\n+line2secret\n+-----END RSA PRIVATE KEY-----\n+x = 1"
    text, _ = redact(block)
    assert "MIIE" not in text and "line2secret" not in text and "BEGIN" not in text
    assert text.endswith("+x = 1")


def test_key_block_without_end_is_removed_to_the_end():
    text, _ = redact("+-----BEGIN PRIVATE KEY-----\n+MIIEsecretpart\n+more")
    assert "MIIE" not in text and "more" not in text


@pytest.mark.parametrize("line, secret", [
    ('API_KEY = "my-real-api-key-value"', "my-real-api-key-value"),
    ("SECRET_KEY=abc123", "abc123"),
    ("DB_PASSWORD: 'p4ss-w0rd'", "p4ss-w0rd"),
    ('"github_token": "tok3n-value"', "tok3n-value"),
    ("const authToken = 'xyz789';", "xyz789"),
])
def test_values_given_to_secret_names_are_hidden(line, secret):
    text, count = redact("+" + line)
    assert secret not in text and REDACTED in text and count == 1


@pytest.mark.parametrize("line", [
    "token = get_token()",
    'key = os.environ["API_KEY"]',
    "password = user_input",
    "def check_password(password):",
    "if token == expected:",
    "api_key: str = None",
    "SECRET_KEY = settings.SECRET_KEY",
])
def test_harmless_code_is_left_alone(line):
    assert redact("+" + line) == ("+" + line, 0)


def test_fake_key_in_config_py_is_never_sent_and_scored_high(repo):
    repo.write("config.py", "DEBUG = False\n")
    repo.write("app.py", "x = 1\n")
    repo.commit()
    snapshot.take(repo.root)
    key = FAKE_KEYS["OpenAI (sk-proj-)"]
    repo.write("config.py", f'DEBUG = False\nAPI_KEY = "{key}"\n')
    repo.write("app.py", "token = get_token()\n")
    found = find_changes(repo.root, store.load_table(repo.root))
    message = build_message(ai_input(found, {})[0])
    assert key not in message
    assert 'API_KEY = "[REDACTED]"' in message
    assert "+token = get_token()" in message  # harmless code still reaches the AI
    scores = {c.path: rules.score(rules.rules_for(c, found)) for c in found}
    config = next(c for c in found if c.path == "config.py")
    assert scores["config.py"] == "HIGH"
    assert ("HIGH", "possible secret (value hidden)") in rules.rules_for(config, found)
    assert scores["app.py"] == "LOW"


def test_every_platform_key_in_one_file_never_reaches_the_message():
    body = "\n".join(f"+{name.split()[0].upper()}_VAL = connect('{key}')" for name, key in FAKE_KEYS.items())
    c = Change(path="settings_loader.py", kind="modified", file_kind=file_kind("settings_loader.py"),
               diff="--- a\n+++ b\n@@ -1 +1 @@\n" + body)
    message = build_message(ai_input([c], {})[0])
    for platform, key in FAKE_KEYS.items():
        assert key not in message, platform
