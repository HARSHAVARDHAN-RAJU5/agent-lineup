"""Keeps secrets away from lineup's output and the LLM. Private files are flagged by name only."""

import fnmatch
import posixpath

# Matched against the file name only, case-insensitive.
PRIVATE_NAMES = [
    ".env", ".env.*", "*.env",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "*.jks",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    "credentials*", "*secret*", "*.secrets",
    ".npmrc", ".pypirc", ".netrc", ".htpasswd",
]

# Templates that are meant to be shared.
SAFE_NAMES = [".env.example", ".env.sample", ".env.template", "*.example", "*.sample"]


def is_private(path):
    name = posixpath.basename(path).lower()
    if any(fnmatch.fnmatchcase(name, p) for p in SAFE_NAMES):
        return False
    return any(fnmatch.fnmatchcase(name, p) for p in PRIVATE_NAMES)
