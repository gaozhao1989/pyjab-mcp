#!/usr/bin/env python
"""Check that ``java_diagnostics`` reports the truth on a real JDK.

Why this exists
---------------

PLAN §10 makes "``java_diagnostics`` reports the environment correctly under JDK 8 / 17 / 21"
a release criterion, and the failure it guards against is the one this project keeps
finding: a report that cannot tell "the DLL is not there" from "I could not look" is worse
than no report, because it sends the user to fix the wrong thing.

The three JDKs differ in a way that matters to the search, not just in version number:

* **JDK 8** keeps the bridge in ``%JAVA_HOME%\\jre\\bin\\WindowsAccessBridge-64.dll``;
* **JDK 11 and newer** keep it in ``%JAVA_HOME%\\bin\\``;
* a **standalone JAB** lives under ``%JAB_HOME%``.

So this runs on Windows under each JDK ``actions/setup-java`` installs and asserts that the
report is ``OK`` and that the path it found is inside that JDK — which is the difference
between "a DLL was found" and "the right DLL was found".

Off Windows it prints ``UNAVAILABLE`` and exits 0: this is a Windows check, and pretending
otherwise would make the script lie in the other direction.

Usage
-----

    python tools/verify_diagnostics_jdk.py            # report and assert
    python tools/verify_diagnostics_jdk.py --json      # the rows, machine-readable
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:                    # a checkout, not an install
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pyjab_mcp.diagnostics import Status, collect          # noqa: E402


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the rows as JSON")
    args = parser.parse_args(argv)

    report = collect()
    if args.json:
        print(json.dumps(
            [{"label": row.label, "value": row.value, "status": row.status,
              "detail": row.detail} for row in report.rows],
            indent=2, ensure_ascii=False,
        ))
    else:
        print(report.render())

    rows = {row.label: row for row in report.rows}
    platform_row = rows.get("platform")
    if platform_row is None or platform_row.status == Status.UNAVAILABLE:
        print("\nUNAVAILABLE: this check only means something on Windows.")
        return 0

    problems: List[str] = []
    dll_row = next((row for row in report.rows if row.label.startswith("bridge DLL")), None)
    if dll_row is None:
        problems.append("the report has no bridge-DLL row at all")
    elif dll_row.status != Status.OK:
        problems.append(
            f"the bridge DLL was not found ({dll_row.status}: {dll_row.value}). "
            f"java_diagnostics has to locate it for the user, so this is the report "
            f"failing, not just the environment. Search: {dll_row.detail}"
        )
    else:
        java_home = os.environ.get("JAVA_HOME", "")
        found = Path(dll_row.value)
        if java_home and Path(java_home).resolve().as_posix().lower() not in \
                found.resolve().as_posix().lower():
            problems.append(
                f"the DLL was found outside JAVA_HOME ({found} vs {java_home}). "
                "Diagnostics is meant to report the DLL paired with the JDK in use, "
                "because a mismatched pair fails as 'pyjab cannot see my window'."
            )

    access_row = rows.get("Java Access Bridge enabled")
    if access_row is None:
        problems.append("the report has no Java-Access-Bridge row at all")
    elif access_row.status == Status.UNAVAILABLE:
        problems.append(
            f"the JAB-enabled state could not be read ({access_row.value}); "
            "'I could not read it' is not a verdict, but on Windows it is a bug"
        )

    if problems:
        print()
        for problem in problems:
            print(f"::error:: {problem}")
        return 1

    print(f"\nPASSED: diagnostics located {dll_row.value if dll_row else '?'} "
          "and read the JAB state, on this JDK.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
