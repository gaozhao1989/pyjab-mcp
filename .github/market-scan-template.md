<!-- Opened by .github/workflows/market-scan.yml every two weeks, or by hand. -->

**A scan is due.** This is a research work order: look outward, and report what you found as
**evidence**.

It is deliberately self-contained, because this file travels in the published sdist and a reader there
cannot open the maintainer's local notes. Keep it that way: **do not add references to files outside
this repository's tracked tree** — a check (`tools/check_published_references.py`) refuses the release
when a shipped file points at one.

## The rules that decide whether this is worth anything

* **Every factual claim carries a URL, a date, and the words it rests on.** Keep three labels apart:
  **measured** (a number you reproduced — say what you queried), **read** (something published — quote
  and link it), **inferred** (your judgement, in its own section, never dressed as the other two).
* **"I found nothing" is a result, and often the true one.** For a project this small, "what do users
  say about us" is very likely "nothing findable, because there is nothing yet". Say where you looked.
* **Never present our own published text as user feedback.** Search results will mostly be our README,
  our registry entry and pyjab's docs. If our text is the only source, write "no independent source".
* **A tracker search is a snapshot: say when you searched**, and search the *subject* rather than our
  wording for it.

## Where it pays to look

1. **`pyjab`'s tracker and changelog** (`gaozhao1989/pyjab`) — our users' environment is pyjab's, so a
   problem there is a problem here waiting to happen, and it is citable.
2. **Comparable capability** — what already does this job (other GUI-automation MCP servers, Windows
   UI-automation libraries, JAB/UIA wrappers) and how it is packaged. The useful question is not who is
   better but **what would we be duplicating, and what do people demonstrably lack**.
3. **Ecosystem placement** — registries and aggregators: listed? under what category? does the listing
   say a user needs Windows and a JDK?
4. **Our own measurable artifacts** — PyPI's JSON, download counts (**with the sample size**), the
   registry's `status`/`isLatest`, stars and issue counts; and the gaps our own docs admit, which are
   the cheapest true signal there is.

## The report stays local; this issue is a summary

**This is a published page, and a scan produces competitive analysis, market measurements and
quotations from other projects. None of that belongs here.** Keep the report itself in the maintainer's
working notes and put only the following on this issue:

1. one line saying the scan ran, and the date;
2. whichever candidate issues were accepted, **linked**;
3. any decision taken, with its reasoning and **what would change it**.

The full report -- the numbers, the sources, the per-line verdicts, the quotations -- stays local. If a
candidate needs a number to be understood, put the number in the candidate's own issue and nothing else,
and never a competitor's name or figure: this project does not publish its competitive position, and a
tracker is not a place to start.

If you are unsure whether something is safe to write here, leave it out and say so in the summary.
