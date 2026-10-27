# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Documented example outputs must match what the examples print.

Each example listed in ``examples/README.md`` and the code block in
``docs/getting_started.md`` is run as a subprocess. Its printed output is compared with the
values documented next to it. The documented values are parsed from the docs themselves, so
updating a doc and the code that produces it is a single change.

A script that exits nonzero, prints no value, or has no documented row fails its test.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "examples" / "README.md"
GETTING_STARTED = ROOT / "docs" / "getting_started.md"

# README table key -> example script. The key is the backticked name in the table's first column.
README_EXAMPLES = {
    "_simple": "nuclear_uprate_project_simple.py",
    "_full": "nuclear_uprate_project.py",
    "_midlevel": "nuclear_uprate_project_midlevel.py",
    "_builders": "nuclear_uprate_project_builders.py",
    "_primitives": "nuclear_uprate_project_primitives.py",
}

README_NPV_TOLERANCE = 1e6  # README values are approximate ("~$307 M")
GETTING_STARTED_NPV_TOLERANCE = 1.0  # dollars
GETTING_STARTED_LCOE_TOLERANCE = 0.005  # dollars per MWh

# "| `_full` (high-level) | ~$37 M (...) |" -> key "_full", sign "", amount "37"
_README_ROW = re.compile(r"^\|\s*`(_\w+)`[^|]*\|\s*~?\s*([−-]?)\s*\$\s*([\d.]+)\s*M")
# "NPV ($):    375,736,502" in example output
_PRINTED_NPV = re.compile(r"^NPV[^:]*:\s*\$?\s*(-?[\d,]+(?:\.\d+)?)", re.MULTILINE)
# "NPV:   $318,988,351" and "LCOE:  $23.30/MWh" in both the documented and printed output
_NPV = re.compile(r"^NPV:\s*\$\s*(-?[\d,]+(?:\.\d+)?)", re.MULTILINE)
_LCOE = re.compile(r"^LCOE:\s*\$\s*(-?[\d,]+(?:\.\d+)?)", re.MULTILINE)


def _run(script: Path, cwd: Path) -> str:
    """Run ``script`` with this interpreter against this checkout and return its stdout.

    Fails the calling test, with the script's output, if the process exits nonzero.
    """
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, (
        f"{script.name} exited with {result.returncode}:\n{result.stdout}{result.stderr}"
    )
    return result.stdout


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _first_match(pattern: re.Pattern[str], text: str, what: str) -> float:
    found = pattern.search(text)
    assert found is not None, f"no {what} found in:\n{text}"
    return _number(found.group(1))


def _documented_readme_npv(key: str) -> float:
    """Return the NPV (in dollars) documented for ``key`` in the README comparison table."""
    documented = []
    for line in README.read_text().splitlines():
        row = _README_ROW.match(line.strip())
        if row is not None and row.group(1) == key:
            sign = -1 if row.group(2) else 1
            documented.append(sign * float(row.group(3)) * 1e6)
    assert len(documented) == 1, f"expected one documented NPV row for {key}, found {documented}"
    return documented[0]


@pytest.mark.parametrize("key", README_EXAMPLES)
def test_readme_npv_matches_example(key: str) -> None:
    documented = _documented_readme_npv(key)
    printed = _first_match(
        _PRINTED_NPV,
        _run(ROOT / "examples" / README_EXAMPLES[key], cwd=ROOT),
        "printed NPV",
    )
    assert printed == pytest.approx(documented, abs=README_NPV_TOLERANCE), (
        f"examples/README.md documents {key} NPV as ${documented:,.0f}; "
        f"the example prints ${printed:,.0f}"
    )


def test_getting_started_matches_code_block(tmp_path: Path) -> None:
    text = GETTING_STARTED.read_text()
    code = re.search(r"```python\n(.*?)```", text, re.DOTALL)
    expected = re.search(r"```text\n(.*?)```", text, re.DOTALL)
    assert code is not None, "docs/getting_started.md has no python code block"
    assert expected is not None, "docs/getting_started.md has no expected-output text block"

    script = tmp_path / "first_analysis.py"
    script.write_text(code.group(1))
    printed = _run(script, cwd=tmp_path)

    doc_npv = _first_match(_NPV, expected.group(1), "documented NPV")
    doc_lcoe = _first_match(_LCOE, expected.group(1), "documented LCOE")
    npv = _first_match(_NPV, printed, "printed NPV")
    lcoe = _first_match(_LCOE, printed, "printed LCOE")

    mismatches = []
    if npv != pytest.approx(doc_npv, abs=GETTING_STARTED_NPV_TOLERANCE):
        mismatches.append(f"NPV documented ${doc_npv:,.0f}, printed ${npv:,.0f}")
    if lcoe != pytest.approx(doc_lcoe, abs=GETTING_STARTED_LCOE_TOLERANCE):
        mismatches.append(f"LCOE documented ${doc_lcoe:,.2f}/MWh, printed ${lcoe:,.2f}/MWh")
    assert not mismatches, "docs/getting_started.md differs from its code block: " + "; ".join(
        mismatches
    )
