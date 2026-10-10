#!/usr/bin/env python
"""Fetch pyjab's test application source, from the sdist of the installed pyjab.

Why this exists
---------------

L3 needs a real Swing application, and the only one available to a hosted runner is pyjab's
own test app. Its source ships in pyjab's sdist (``recursive-include tests *.java``), and
pyjab's GUI suite compiles that same file on this same runner -- so it is a known-good target
rather than a second Java program nobody has built.

**The version comes from the installed wheel**, not from a guess: the application is compiled
by the same JDK that will drive it, but the *file* has to match the release whose bridge is
installed, or a test failure would be ambiguous between the two.

It is a script rather than a few lines of shell in the workflow because shell in a workflow
is untestable, and this has already gone wrong once in the shape of a PowerShell here-string
holding Python.

Usage
-----

    python tools/fetch_test_app.py --out app-src        # prints the path it wrote
"""

from __future__ import annotations

import argparse
import importlib.metadata
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Optional, Sequence

MEMBER_SUFFIX = "tests/java/PyjabTestApp.java"


def fetch(*, version: Optional[str] = None, out: Optional[Path] = None,
          python: Optional[str] = None) -> Path:
    """Extract ``PyjabTestApp.java`` and return the path it was written to.

    Args:
        version: The pyjab version to fetch. Defaults to the installed one, which is the
            only correct answer for an end-to-end test: the app and the bridge must be from
            the same release.
        out: Directory for the extracted file. Defaults to a temporary directory.
        python: Interpreter used to run pip. Defaults to the running one, because pyjab is
            installed *in it* and pip has to see the same environment.

    Raises:
        RuntimeError: When the download fails or the sdist does not carry the file. Both
            are real answers rather than things to work around silently.
    """
    version = version or importlib.metadata.version("pyjab")
    out = Path(out or Path(tempfile.mkdtemp(prefix="pyjab-test-app-")))
    out.mkdir(parents=True, exist_ok=True)
    target = out / "PyjabTestApp.java"
    if target.is_file():
        return target

    with tempfile.TemporaryDirectory(prefix="pyjab-sdist-") as download_dir:
        completed = subprocess.run(
            [python or sys.executable, "-m", "pip", "download", f"pyjab=={version}",
             "--no-deps", "--no-binary", ":all:", "-d", str(download_dir)],
            capture_output=True, text=True, timeout=900,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"pip download pyjab=={version} failed:\n"
                f"{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}"
            )
        tarball = next(iter(sorted(Path(download_dir).glob("pyjab-*.tar.gz"))), None)
        if tarball is None:
            raise RuntimeError(
                f"no pyjab sdist appeared in {download_dir}; pip said:\n"
                f"{completed.stdout[-2000:]}"
            )

        with tarfile.open(tarball) as tar:
            member = next(
                (m for m in tar.getmembers() if m.name.endswith(MEMBER_SUFFIX)), None
            )
            if member is None:
                raise RuntimeError(f"{tarball.name} does not contain {MEMBER_SUFFIX}")
            extracted = tar.extractfile(member)
            if extracted is None:  # pragma: no cover - a directory, not a file
                raise RuntimeError(f"{member.name} is not a regular file")
            target.write_bytes(extracted.read())

    return target


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default=None,
                        help="pyjab version to fetch (default: the installed one)")
    parser.add_argument("--out", default=None, help="directory to write into")
    args = parser.parse_args(argv)

    try:
        path = fetch(
            version=args.version,
            out=Path(args.out) if args.out else None,
        )
    except RuntimeError as exc:
        print(f"::error:: {exc}")
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
