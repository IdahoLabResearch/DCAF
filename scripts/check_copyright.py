# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Check tracked Python files for the required copyright header."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

COPYRIGHT_HEADER = (
    "# © 2026 Battelle Energy Alliance, LLC",
    "# ALL RIGHTS RESERVED",
)
ENCODING_DECLARATION = re.compile(r"^#.*?coding[:=][ \t]*[-_.a-zA-Z0-9]+")


def tracked_python_files() -> list[Path]:
    """Return Python files tracked by the current Git repository."""
    result = subprocess.run(
        ["git", "ls-files", "--", "*.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [Path(filename) for filename in result.stdout.splitlines()]


def expected_header_line(lines: list[str]) -> int:
    """Return the zero-based line where the header must begin."""
    line = 0
    if lines and lines[0].startswith("#!"):
        line += 1
    if line < len(lines) and line < 2 and ENCODING_DECLARATION.match(lines[line]):
        line += 1
    return line


def has_copyright_header(path: Path) -> tuple[bool, int]:
    """Return whether *path* has the exact header and its expected line number."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header_line = expected_header_line(lines)
    actual_header = tuple(lines[header_line : header_line + len(COPYRIGHT_HEADER)])
    return actual_header == COPYRIGHT_HEADER, header_line + 1


def main(paths: list[str]) -> int:
    """Check requested paths, or all tracked Python files when none are given."""
    files = [Path(path) for path in paths if Path(path).suffix == ".py"]
    if not paths:
        files = tracked_python_files()

    failed = False
    for path in files:
        try:
            valid, expected_line = has_copyright_header(path)
        except (OSError, UnicodeError) as error:
            print(f"{path}: could not check copyright header: {error}", file=sys.stderr)
            failed = True
            continue

        if not valid:
            print(
                f"{path}:{expected_line}: missing or incorrect copyright header",
                file=sys.stderr,
            )
            failed = True

    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
