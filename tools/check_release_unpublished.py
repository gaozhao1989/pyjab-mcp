#!/usr/bin/env python
"""Decide whether this version is still unpublished, before anything is published.

Why this exists
---------------

``github-release`` in ``.github/workflows/release.yml`` needs only ``verify``, while the
``pypi`` job -- which is where a duplicate version is actually refused -- is a separate job
behind a human approval. So a tag for a version that is already on PyPI reaches the release
job *first* and creates a GitHub release with no matching package: the half-published state
the pre-flight in ``docs/RELEASING.md`` exists to prevent. That pre-flight was a line a
person remembered and the pipeline never did -- ``grep -nE "pypi\\.org/pypi" release.yml``
had no hits.

The verdict is here rather than in a workflow ``run:`` block for the same reason the L3 gate
is (``tools/check_release_l3.py``): every branch below has to be exercised by
``tests/test_guards.py``, and a check whose failure path has never run is decoration.

Usage
-----

::

    BODY="$(mktemp)"
    URL="https://pypi.org/pypi/pyjab-mcp/${VERSION}/json"
    CODE="$(curl -sS -o "$BODY" -w '%{http_code}' "$URL")" || CODE="000"
    python tools/check_release_unpublished.py --version "$VERSION" \\
        --pypi-code "$CODE" --pypi-body "$BODY"

``--pypi-code`` is the status curl *reported*, so it has to come from a command
substitution. A redirect would make ``$CODE`` a path -- ``curl ... > "$CODE"`` -- and the
tool would report ``(HTTP /tmp/tmp.XXXX)`` instead of the status.

The verdicts, and why each is a refusal
---------------------------------------

* ``404``: **proceed** -- the version is not on PyPI. The URL that was checked is printed,
  so the log says what "unpublished" was measured against.
* ``200``: **refuse** -- the version is already published, and a published version can never
  be replaced, so the release needs a *new* version. The index's own answer is printed, and
  when it disagrees with ``--version`` the disagreement is named too: that is the check
  reading the wrong version, and a silent "already published" would hide it.
* any other code (``000``, 5xx, 403, ...): **refuse as unread** -- ``the index could not be
  read ... unverified, which is not the same as 'not published'``. A network failure during
  a release is not permission to continue: an index that could not be read is not an index
  that said the version is absent.
* ``200`` with a body that cannot be parsed: still **refuse**, because the status said the
  version is there and the body is not what decides that -- but the line leads with what was
  seen (an HTTP 200 whose body is not PyPI's JSON, which a proxy or a captive portal
  produces) rather than with the verdict, so the two states cannot be confused.

``--release-code`` is the same status for ``GET /repos/<owner>/<repo>/releases/tags/v<version>``
and is **informational only: it never changes the verdict and never fails the run.** A
GitHub release that already exists is a legitimate state -- on a re-run of a tag whose
release was already created, or after a pending deployment approval -- and ``github-release``
needs only ``verify``, so refusing here would block a release that is otherwise correct. It
is printed because it is the other half of "nothing is half-published", not because it
decides anything.

``--dry-run`` is for ``workflow_dispatch``: every refusal becomes a ``::warning::`` and the
exit status stays 0, which is the contract the version guard and the L3 gate in the same job
already have. It changes the annotation and the exit status, never the verdict -- so a green
dry run does not mean "not published"; look for ``PASSED:`` in the log.

Exit code 0 when the release may proceed, non-zero when it must not.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence

#: The project this release belongs to, named in the URL the tool prints so a log reader can
#: see exactly what was asked and where.
PROJECT = "pyjab-mcp"
INDEX = "https://pypi.org/pypi"


class Decision(NamedTuple):
    """The verdict, and the lines that explain it."""

    exit_code: int
    lines: "List[str]"


def _refusal(dry_run: bool) -> int:
    """What a refusal exits with: 0 on a dry run, 1 on a tag push."""
    return 0 if dry_run else 1


def _verdict(dry_run: bool) -> str:
    """The annotation a refusal carries: a warning on a dry run, an error on a tag push."""
    return "warning" if dry_run else "error"


def index_url(version: str) -> str:
    """The PyPI JSON endpoint the pre-flight in ``docs/RELEASING.md`` already used."""
    return f"{INDEX}/{PROJECT}/{version}/json"


def _reported_version(body: Path) -> "Optional[str]":
    """The version the index says it answered for, or None when the body has no usable one.

    Read best effort: this is evidence printed next to a verdict the *status* already
    decided, so a body that is not JSON, or JSON without ``info.version``, is not a second
    failure -- it only means there is nothing extra to show.
    """
    try:
        payload = json.loads(body.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    reported = info.get("version")
    return reported if isinstance(reported, str) else None


def _release_line(release_code: "Optional[str]") -> "List[str]":
    """The informational GitHub-release line, or nothing when it was not asked for.

    Deliberately not a verdict. ``github-release`` needs only ``verify``, so a re-run of a
    tag whose release was already created is a legitimate state in which this reports 200 --
    making it fail would refuse a correct release, and "fixing" it into a failure later would
    be the bug, not the improvement.
    """
    if release_code is None:
        return []
    code = str(release_code).strip()
    if code == "200":
        return [
            "::notice::a GitHub release already exists for this tag -- expected on a re-run "
            "after a pending approval, and not a reason to refuse (docs/RELEASING.md)"
        ]
    if code == "404":
        return ["the GitHub release does not exist yet (HTTP 404)"]
    return [
        f"::notice::the GitHub release could not be read (HTTP {code}); "
        "informational only, this never changes the verdict"
    ]


def decide(
    version: str,
    pypi_code: str,
    pypi_body: Path,
    release_code: "Optional[str]" = None,
    dry_run: bool = False,
) -> Decision:
    """Read the index's answer and return whether the release may proceed.

    *pypi_body* is the file the ``curl`` wrote, *pypi_code* is the status ``curl`` reported
    (``000`` when there was no HTTP response at all), and *version* is the version the tag
    names. *release_code* is printed but never decides. *dry_run* downgrades every refusal to
    a warning without changing the text.
    """
    lines: "List[str]" = []
    verdict = _verdict(dry_run)
    code = str(pypi_code).strip()
    url = index_url(version)
    # Tracked rather than inferred from the text: the exit status must not depend on how a
    # message happens to be worded.
    refused = False

    if code == "404":
        lines.append(
            f"PASSED: {PROJECT} {version} is not on PyPI -- the index answered 404 for {url}"
        )
    elif code == "200":
        # The status decides this, not the body: a published version cannot be replaced, so
        # a body this tool cannot read is a reason to look, not a reason to continue.
        refused = True
        reported = _reported_version(pypi_body)
        if reported is None:
            # Lead with what was seen, not with the verdict: PyPI's own 200 always parses, so an
            # unparsable one is a proxy or a captive portal answering instead. The decision is
            # the same refusal, but the headline must not tell a maintainer that this index said
            # the version is there when what actually happened is that the answer was not read.
            lines.append(
                f"::{verdict}::the index answered 200 for {url} but the body could not be "
                f"parsed -- treating {version} as already published; confirm by hand before "
                "bumping a version. A published version cannot be replaced"
            )
        elif reported == version:
            lines.append(
                f"::{verdict}::{PROJECT} {version} is already published ({url}) -- a published "
                f"version cannot be replaced, so this release needs a new version. The index "
                f"reports version {reported}"
            )
        else:
            lines.append(
                f"::{verdict}::{PROJECT} {version} is already published ({url}) -- a published "
                "version cannot be replaced, so this release needs a new version. "
                f"Note: the index reports version {reported!r}, not {version!r} -- this check "
                "is confused about which version it asked for"
            )
    else:
        refused = True
        lines.append(
            f"::{verdict}::the index could not be read for {PROJECT} {version} (HTTP {code}, "
            f"{url}) -- this check is unverified, which is not the same as 'not published'"
        )

    lines.extend(_release_line(release_code))
    return Decision(_refusal(dry_run) if refused else 0, lines)


def main(argv: "Optional[Sequence[str]]" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="the version the tag names, without the v")
    parser.add_argument(
        "--pypi-code", required=True,
        help="the status curl reported for the PyPI JSON API; 000 means no HTTP response at all",
    )
    parser.add_argument(
        "--pypi-body", required=True,
        help="the file the curl wrote the index's answer to",
    )
    parser.add_argument(
        "--release-code", default=None,
        help="the status curl reported for the GitHub release, informational only",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report every refusal as ::warning:: and exit 0 (workflow_dispatch)",
    )
    args = parser.parse_args(argv)

    decision = decide(
        args.version,
        args.pypi_code,
        Path(args.pypi_body),
        release_code=args.release_code,
        dry_run=args.dry_run,
    )
    for line in decision.lines:
        print(line)
    return decision.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
