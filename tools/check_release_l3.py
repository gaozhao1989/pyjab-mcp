#!/usr/bin/env python
"""Decide whether the tagged commit was ever driven end to end (L3).

Why this exists
---------------

L3 is the only layer that can say a tool call worked against a live window, and
``windows-gui.yml`` runs weekly (and on dispatch) rather than as a pull request gate, so a
green default CI run says nothing about it. "A green L3 run on the tagged commit" used to be
a step in the release procedure that a person remembered; ``verify`` in
``.github/workflows/release.yml`` asks the Actions API instead, because PyPI cannot take a
release back.

That verdict was fifteen cases of Python inside a workflow ``run:`` block, where nothing in
the repository could test it -- and a check whose failure path has never run is decoration.
It lives here so it can be pinned by ``tests/test_guards.py`` and so the workflow keeps only
what is not a verdict: the ``push``/``workflow_dispatch`` decision, the ``curl``, and passing
these arguments.

Usage
-----

    curl -sS -o "$BODY" -w '%{http_code}' <runs URL> > "$CODE"
    python tools/check_release_l3.py --body "$BODY" --http-code "$CODE" --sha "$GITHUB_SHA"

The verdicts, and why each is a refusal
---------------------------------------

* a run with ``conclusion == "success"`` among the candidates: **proceed**, naming the run id
  and its URL, so the log says which run the release was allowed on;
* candidates exist and none succeeded: **refuse**, listing every candidate's id, status and
  conclusion;
* ``total_count`` greater than the number of runs read: **refuse** -- a page was truncated, so
  a green run cannot be ruled out. This is deliberately *not* "no green run";
* no runs at all: **refuse**, because the commit was never driven end to end;
* HTTP code other than 200 (including ``000``, no response at all): **refuse as unread**, which
  is not the same as "no green run". A 401/403 keeps the hint that names ``actions: read`` and
  says a dry run does not prove the permission;
* 200 with a body that cannot be parsed or does not have the documented shape: **refuse as
  unread**, for the same reason.

``--dry-run`` is for ``workflow_dispatch``, which runs on ``main``'s HEAD where no L3 run
normally exists yet: every refusal becomes a ``::warning::`` and the exit status stays 0, which
is the contract the version guard in the same job already has. That is why a green dry run does
**not** prove the ``actions: read`` permission -- look for ``PASSED: run <id>`` in a run whose
commit really has an L3 run.

Exit code 0 when the release may proceed, non-zero when it must not.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence

#: How much of a non-200 answer to print. The workflow printed this before the logic moved
#: here, and it is the only evidence a maintainer gets for an API error.
BODY_PREVIEW = 2000

#: The workflow this gate asks about. Named in the messages so a log reader knows which run
#: was being looked for.
WORKFLOW = "windows-gui.yml"


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


def _body_preview(body: Path) -> "List[str]":
    """The head of the answer, best effort: this runs on the path where it is already bad."""
    try:
        text = body.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text:
        return []
    return [text[:BODY_PREVIEW]]


def decide(body: Path, http_code: str, sha: str, dry_run: bool = False) -> Decision:
    """Read the Actions API answer in *body* and return whether the release may proceed.

    *body* is the file the ``curl`` wrote, *http_code* is the status ``curl`` reported
    (``000`` when there was no HTTP response at all), and *sha* is the commit the tag points
    at. *dry_run* downgrades every refusal to a warning without changing the text.
    """
    lines: "List[str]" = []
    verdict = _verdict(dry_run)
    code = str(http_code).strip()

    if code != "200":
        # Not a verdict about L3: the gate could not be read.
        lines.append(
            f"::{verdict}::the Actions API could not be read for {WORKFLOW} at {sha} "
            f"(HTTP {code}) -- this gate is unverified, which is not the same as satisfied"
        )
        if code in ("401", "403"):
            # Name the likely cause, because the message above is all a maintainer would see.
            # And say what a dry run can and cannot prove: on `workflow_dispatch` this branch
            # exits 0 with a warning, so a green dry run does NOT prove the permission -- the
            # proof is `PASSED: run <id>` in the log of a run whose commit has an L3 run.
            lines.append(
                f"::notice::HTTP {code} from the Actions API usually means this workflow is "
                "missing 'actions: read'. Note that a dry run downgrades this to a warning, "
                "so a green dry run does not prove the permission -- look for "
                "'PASSED: run <id>' instead."
            )
        lines.extend(_body_preview(body))
        return Decision(_refusal(dry_run), lines)

    try:
        payload = json.loads(body.read_text(encoding="utf-8"))
        runs = payload["workflow_runs"]
        total = payload["total_count"]
        if not isinstance(runs, list) or not isinstance(total, int):
            raise ValueError("workflow_runs/total_count have an unexpected shape")
        if any(not isinstance(run, dict) for run in runs):
            # A list of something other than runs is as unreadable as unparsable JSON, and
            # `run.get` on it would be a traceback rather than a verdict.
            raise ValueError("workflow_runs entries have an unexpected shape")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        lines.append(
            f"::{verdict}::the Actions API answer could not be read ({exc!r}) -- unverified, "
            "which is not the same as satisfied"
        )
        return Decision(_refusal(dry_run), lines)

    lines.append(f"{WORKFLOW} at {sha}: {total} run(s) match, {len(runs)} read")
    for run in runs:
        lines.append(
            f"  run {run.get('id')}: event={run.get('event')} status={run.get('status')} "
            f"conclusion={run.get('conclusion')} {run.get('html_url')}"
        )

    # Every run, not just the first: a later candidate can be the green one.
    green = [run for run in runs if run.get("conclusion") == "success"]
    if green:
        lines.append(
            f"PASSED: run {green[0].get('id')} of {WORKFLOW} succeeded at {sha} -- "
            f"{green[0].get('html_url')}"
        )
        return Decision(0, lines)

    if total > len(runs):
        # A run whose conclusion could not be read is not a green run, and candidates
        # that were never read are not "no green run" either.
        reason = (
            f"{total} runs match {sha} but only {len(runs)} were read, so a green "
            f"one cannot be ruled out"
        )
    elif runs:
        rejected = ", ".join(
            f"{run.get('id')} ({run.get('status')}/{run.get('conclusion')})" for run in runs
        )
        reason = f"no run of {WORKFLOW} at {sha} succeeded; rejected: {rejected}"
    else:
        reason = (
            f"no {WORKFLOW} run exists at {sha}: this commit was never driven "
            f"end to end -- dispatch it and let it pass before tagging"
        )
    lines.append(f"::{verdict}::{reason}")
    return Decision(_refusal(dry_run), lines)


def main(argv: "Optional[Sequence[str]]" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--body", required=True, help="the file the curl wrote the answer to")
    parser.add_argument(
        "--http-code", required=True,
        help="the status curl reported; 000 means there was no HTTP response at all",
    )
    parser.add_argument("--sha", required=True, help="the commit the tag points at")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report every refusal as ::warning:: and exit 0 (workflow_dispatch)",
    )
    args = parser.parse_args(argv)

    decision = decide(
        Path(args.body), args.http_code, args.sha, dry_run=args.dry_run
    )
    for line in decision.lines:
        print(line)
    return decision.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
