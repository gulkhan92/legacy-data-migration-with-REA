"""Look for committed secrets: provider keys, private keys, signed tokens and credentials assigned to names that say what they are.

    python scripts/secrets_scan.py            # the working tree (tracked and new files, not ignored ones)
    python scripts/secrets_scan.py --history  # also every commit of every branch

Exit status 1 when something is found. A finding is a line, never printed with its value: the file, the line and the kind are shown.
Values that are plainly placeholders (the development defaults, `change-me`, `example`, values in tests that exist to be found) are allowed
by the lists below, each with the reason, so the scan stays quiet about them and loud about anything else.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = {
    "Groq key": re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    "signed token": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "credential in a URL": re.compile(r"[a-z]+://[^/\s:@]+:[^/\s:@]{6,}@(?!localhost|127\.0\.0\.1)[\w.-]+"),
    "assigned secret": re.compile(r"""(?i)\b(?:api[_-]?key|secret|passw(?:or)?d|token)\b["']?\s*[:=]\s*["']([A-Za-z0-9+/=_\-!@#$%^&*.]{16,})["']"""),
}

# Values that are not secrets, with the reason. Matched against the value that was found.
PLACEHOLDER = re.compile(r"(?i)change[-_ ]?me|example|placeholder|dev-only|your[-_]|<[^>]+>|\$\{|xxxx|0123456789|not-the-password|correct-horse|sentinel|test")

# Files that hold values on purpose: the lines are checked for placeholders, not skipped.
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "var", "__pycache__", ".pytest_cache", "playwright-report", "test-results", "lighthouse-reports", "raw"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2", ".sqlite", ".dll", ".cdb", ".zip", ".gz", ".pdf", ".lock", ".svg", ".webm", ".mp4"}


def files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    return [ROOT / f for f in out if not (set(Path(f).parts) & SKIP_DIRS) and Path(f).suffix.lower() not in SKIP_SUFFIXES and not Path(f).name.endswith("package-lock.json")]


def scan_text(label: str, text: str) -> list[str]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if len(line) > 2000:
            continue
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                value = match.group(1) if match.groups() else match.group(0)
                if PLACEHOLDER.search(value) or PLACEHOLDER.search(line) and kind in ("assigned secret", "credential in a URL"):
                    continue
                found.append(f"{label}:{number}: {kind}")
    return found


def scan_tree() -> list[str]:
    found: list[str] = []
    for path in files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found += scan_text(str(path.relative_to(ROOT)), text)
    return found


def scan_history() -> list[str]:
    found: list[str] = []
    commits = subprocess.run(["git", "rev-list", "--all"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for commit in commits:
        patch = subprocess.run(["git", "show", "--format=", "--unified=0", "--no-color", commit], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        added = "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
        found += scan_text(f"commit {commit[:8]}", added)
    return found


def main() -> int:
    findings = scan_tree()
    if "--history" in sys.argv:
        findings += scan_history()
    for item in findings:
        print(item)
    print(f"{len(findings)} finding(s)" if findings else "No secrets found.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
