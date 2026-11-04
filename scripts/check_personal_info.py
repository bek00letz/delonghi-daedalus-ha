#!/usr/bin/env python3
"""Block a push that would publish personal information.

Runs as a git pre-push hook (see scripts/install_privacy_guard.sh), and in CI
with the general rules only. It looks at every commit and tag about to be
pushed and stops the push if any of them contains:

  - a personal value listed in .git/info/personal-hashes, found anywhere in a
    line (also inside longer text, and values with spaces). Values are stored
    only as "sha256 length" fingerprints, so this check never holds a readable
    copy: it fingerprints every stretch of text of each stored length;
  - an IP address on a protected network: add the first three parts of the
    address (for example 192.168.1) as a value;
  - a sign-in token (a JWT, or a De'Longhi/Gigya session token);
  - a capture, log, Home Assistant storage, app package or certificate file.

.git/info/ is never pushed, so the fingerprints stay on this computer.

  Add a value:   python3 scripts/check_personal_info.py --add   (prompts, hidden)
  Check by hand: python3 scripts/check_personal_info.py --check-all
"""

from __future__ import annotations

import getpass
import hashlib
from pathlib import Path
import re
import subprocess
import sys

ZERO = "0" * 40
MAX_LINE = 4000  # longer lines are checked in their first part only
PATTERNS = {
    "a sign-in token (JWT)": re.compile(r"eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\."),
    "a De'Longhi sign-in session": re.compile(r"\bst2\.s\.[A-Za-z0-9_.-]{20,}"),
}
IPV4 = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d{1,3}\b")
BLOCKED_FILES = re.compile(
    r"(^|/)(captures|apk|\.security-review|\.storage)/"
    r"|(^|/)(home-assistant\.log[^/]*|core\.config_entries)$"
    r"|\.(mitm|flow|apks?|har|pcapng?|pem|key|p12|cer|crt|token)$",
    re.I,
)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def hashes_file() -> Path:
    return Path(git("rev-parse", "--git-path", "info/personal-hashes").strip())


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def load_fingerprints() -> dict[int, set[str]]:
    """Protected values as {length: {sha256, ...}}."""
    path = hashes_file()
    out: dict[int, set[str]] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and not line.startswith("#") and parts[1].isdigit():
            out.setdefault(int(parts[1]), set()).add(parts[0])
    return out


def add_values() -> None:
    path = hashes_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    print("Type each value to protect (hidden), or press Return on an empty line to finish.")
    added = 0
    with path.open("a") as out:
        while value := getpass.getpass("Value: ").strip():
            out.write(f"{fingerprint(value)} {len(value)}\n")
            added += 1
    path.chmod(0o600)
    print(f"Added {added}. Fingerprints are in {path} (not pushed).")


def contains_protected(text: str, fingerprints: dict[int, set[str]]) -> bool:
    """True if any stretch of ``text`` is a protected value (case-insensitive)."""
    low = text[:MAX_LINE].lower()
    for length, hashes in fingerprints.items():
        for start in range(len(low) - length + 1):
            if hashlib.sha256(low[start : start + length].encode()).hexdigest() in hashes:
                return True
    return False


def refs_to_check(stdin_lines: list[str]) -> tuple[list[str], list[str]]:
    """The commits and annotated tags a push would send."""
    commits: list[str] = []
    tags: list[str] = []
    for line in stdin_lines:
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:  # deleting a ref: nothing to check
            continue
        local_ref, local_sha, _, remote_sha = parts
        if local_ref.startswith("refs/tags/") and git("cat-file", "-t", local_sha).strip() == "tag":
            tags.append(local_sha)
        if remote_sha == ZERO:
            out = git("rev-list", local_sha, "--not", "--remotes")
        else:
            out = git("rev-list", f"{remote_sha}..{local_sha}")
        commits += out.split()
    return list(dict.fromkeys(commits)), tags


def check_text(where: str, text: str, fingerprints: dict[int, set[str]]) -> list[str]:
    found = [f"{where} contains {label}" for label, pattern in PATTERNS.items() if pattern.search(text)]
    if fingerprints:
        if contains_protected(text, fingerprints):
            found.append(f"{where} contains one of your protected personal values")
        ip_hashes = fingerprints.get(0, set()) | {h for hs in fingerprints.values() for h in hs}
        if any(fingerprint(net) in ip_hashes for net in IPV4.findall(text)):
            found.append(f"{where} contains an address on your home network")
    return found


def problems_in_commit(commit: str, fingerprints: dict[int, set[str]]) -> list[str]:
    found: list[str] = []
    names = git("show", "-m", "--format=", "--name-only", commit)
    for name in names.splitlines():
        if name and BLOCKED_FILES.search(name):
            found.append(f"adds a blocked file: {name}")
    meta = git("show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", commit)
    found += check_text("the commit author or message", meta, fingerprints)
    # -m: merge commits are checked against each parent too.
    diff = git("show", "-m", "--format=", "--unified=0", "--no-color", commit)
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            found += check_text("a changed line", line[1:], fingerprints)
    return list(dict.fromkeys(found))


def report(commits: list[str], tags: list[str]) -> int:
    fingerprints = load_fingerprints()
    failures: dict[str, list[str]] = {}
    for commit in commits:
        if problems := problems_in_commit(commit, fingerprints):
            failures[git("show", "-s", "--format=%h %s", commit).strip()] = problems
    for tag in tags:
        if problems := check_text("the tag message", git("cat-file", "-p", tag), fingerprints):
            failures[f"tag {tag[:7]}"] = problems
    if not fingerprints and sys.stdin.isatty():
        print("privacy check: no protected values set up yet; run with --add", file=sys.stderr)
    if not failures:
        return 0
    print("\nPush stopped: it would publish personal information.\n", file=sys.stderr)
    for what, problems in failures.items():
        print(f"  {what}", file=sys.stderr)
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
        return report(git("rev-list", "HEAD").split(), [])
    return report(*refs_to_check(sys.stdin.read().splitlines()))


if __name__ == "__main__":
    sys.exit(main())
