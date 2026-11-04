#!/usr/bin/env python3
"""Block a push that would publish personal information.

Runs as a git pre-push hook (see scripts/install_privacy_guard.sh). It looks at
every commit about to be pushed and stops the push if any of them contains:

  - a personal value listed in .git/info/personal-hashes, stored only as
    SHA-256 fingerprints so this check never holds a readable copy;
  - an IP address on a protected network: add the first three parts of the
    address (for example 192.168.1) as a value, and any address in it matches;
  - a sign-in token (JWT), or a capture, app package or certificate file.

.git/info/ is never pushed, so the fingerprints stay on this computer.

  Add a value:   python3 scripts/check_personal_info.py --add   (prompts, hidden)
  Check by hand: python3 scripts/check_personal_info.py --check-all
"""

from __future__ import annotations

import getpass
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ZERO = "0" * 40
TOKEN_SPLIT = re.compile(r"[\s\"'`,;=()\[\]{}<>/\\|]+")
PATTERNS = {
    "a sign-in token (JWT)": re.compile(r"eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\."),
}
IPV4 = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d{1,3}\b")
BLOCKED_FILES = re.compile(
    r"(^|/)(captures|apk)/|\.(mitm|apks?|har|pcapng?|pem|key|p12|cer|crt|token)$", re.I
)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def hashes_file() -> Path:
    return Path(git("rev-parse", "--git-path", "info/personal-hashes").strip())


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def load_fingerprints() -> set[str]:
    path = hashes_file()
    if not path.exists():
        return set()
    return {line.split()[0] for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")}


def add_values() -> None:
    path = hashes_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    print("Type each value to protect (hidden), or press Return on an empty line to finish.")
    added = 0
    with path.open("a") as out:
        while value := getpass.getpass("Value: ").strip():
            out.write(fingerprint(value) + "\n")
            added += 1
    path.chmod(0o600)
    print(f"Added {added}. Fingerprints are in {path} (not pushed).")


def commits_to_push(stdin_lines: list[str]) -> list[str]:
    commits: list[str] = []
    for line in stdin_lines:
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:  # deleting a branch: nothing to check
            continue
        local_sha, remote_sha = parts[1], parts[3]
        if remote_sha == ZERO:
            out = git("rev-list", local_sha, "--not", "--remotes")
        else:
            out = git("rev-list", f"{remote_sha}..{local_sha}")
        commits += out.split()
    return list(dict.fromkeys(commits))


def problems_in(commit: str, fingerprints: set[str]) -> list[str]:
    found: list[str] = []
    meta = git("show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", commit)
    for name in git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit).split("\n"):
        if name and BLOCKED_FILES.search(name):
            found.append(f"adds a blocked file: {name}")
    added = [
        line[1:]
        for line in git("show", "--format=", "--unified=0", "--no-color", commit).splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    for where, text in [("the commit author or message", meta)] + [("a changed line", line) for line in added]:
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                found.append(f"{where} contains {label}")
        if fingerprints and any(
            fingerprint(tok) in fingerprints for tok in TOKEN_SPLIT.split(text) if len(tok) >= 4
        ):
            found.append(f"{where} contains one of your protected personal values")
        if fingerprints and any(fingerprint(net) in fingerprints for net in IPV4.findall(text)):
            found.append(f"{where} contains an address on your home network")
    return list(dict.fromkeys(found))


def report(commits: list[str]) -> int:
    fingerprints = load_fingerprints()
    failures = {c: p for c in commits if (p := problems_in(c, fingerprints))}
    if not fingerprints:
        print("privacy check: no protected values set up yet; run with --add", file=sys.stderr)
    if not failures:
        return 0
    print("\nPush stopped: it would publish personal information.\n", file=sys.stderr)
    for commit, problems in failures.items():
        subject = git("show", "-s", "--format=%h %s", commit).strip()
        print(f"  {subject}", file=sys.stderr)
        for problem in problems:
            print(f"    - {problem}", file=sys.stderr)
    print(
        "\nRemove it from those commits (for example with git commit --amend or an "
        "interactive rebase), then push again.\n",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    if "--add" in sys.argv:
        add_values()
        return 0
    if "--check-all" in sys.argv:
        return report(git("rev-list", "HEAD").split())
    return report(commits_to_push(sys.stdin.read().splitlines()))


if __name__ == "__main__":
    sys.exit(main())
