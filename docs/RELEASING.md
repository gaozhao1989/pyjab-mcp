# Releasing pyjab-mcp

A release is: **bump the version, move the changelog, tag, approve the publish, and let
[`.github/workflows/release.yml`](../.github/workflows/release.yml) do the rest.** The workflow
is tag-driven, so a tag is the only thing that starts a release — and the approval click below is
the only step in it a person performs.

Released versions are immutable — PyPI will not let a version be replaced or removed.
That is why every check runs **before** the first upload, and why the
version guard exists.

## Who confirms what

A release has exactly one human decision in it, and it is not a review of the checks — those are
the pipeline's, and it refuses the release on any of them.

**The pipeline checks, and stops the release if any of these fail:**

* **the four places the version lives agree** — `__version__`, both `server.json` versions and the
  `CHANGELOG.md` section — via `tools/check_release_version.py`;
* **the six guards**, which refuse rather than warn; the seventh script in the same step,
  `tools/check_pyjab_readiness.py`, reports only;
* **the artifact, not the rules** — `python -m build`, `tools/check_dist_contents.py --dist dist`
  and `twine check`;
* **the changelog-immutability record**, so a released section cannot be edited afterwards;
* **a green L3 run on the tagged commit** — the pipeline requires it: `verify` fails when no
  successful `windows-gui.yml` run exists for the commit the tag points at. A green L3 run is
  checked by the pipeline, not by memory.

**The human decides, once: whether this version, at this moment, with this content, should be
published.** The approval click on the `pypi` environment is the *enforcement* of that decision,
not a second review — everything checkable has already been checked, so the click answers only
"ship it".

**The maintainer's agent prepares the release** — and one of those steps is *not* in the pipeline,
which is worth knowing before trusting the list above:

* the bump, the changelog section, the artifact, the L3 dispatch, the evidence;
* **the pre-flight that nothing is already published** under this version (PyPI, the GitHub
  release, the tag) — **the pipeline does not check this**, so it is the one checkable thing that
  has to be done *before* the tag exists. Do it first, from the pre-flight section below. If a
  version is already on PyPI, the run still reaches `github-release` (it needs only `verify`),
  so the outcome is a GitHub release with no matching package: the half-published state this
  check exists to prevent. *(Making it a pipeline check is filed as an issue; it cannot check the
  tag, because the tag is what starts the run.)*

It never needs a person for a checkable step, and it cannot approve the publish.

### The click, and why nothing can stand in for it

The `pypi` environment on `gaozhao1989/pyjab-mcp` has **required reviewers = `gaozhao1989`** and
**`can_admins_bypass: false`**, set 2026-10-10. The `publish to PyPI` job therefore stops and
waits for a human review before it runs `pypa/gh-action-pypi-publish`, and there is no bypass to
reach for. **No agent can satisfy it, including one running with the maintainer's credentials** —
which is the point, because that is exactly what a delegate has.

The other platform gate is deliberately the weaker one. The ruleset `v* tags are never moved or
deleted` (id 24842486, rules `deletion` + `update`) stops a published tag from being moved or
deleted. It does **not** block creation: the release procedure needs to create the tag, and a
delegate shares the maintainer's credentials, so a gate a shared token can bypass would be
decoration. The tag rule is protection for immutability, not a delegation control.

## One-time setup

Two things had to exist before the first tag, and both do. What a person does *per* release is
not setup but the approval click — see "Who confirms what".

**1. A PyPI pending publisher (trusted publishing).** There is no API token in this repository
and there should never be one. On PyPI, add a *pending* publisher for the project name
`pyjab-mcp` with:

| Field | Value |
|---|---|
| Owner | `gaozhao1989` |
| Repository | `pyjab-mcp` |
| Workflow | `release.yml` |
| Environment | `pypi` |

The environment name is part of what PyPI checks, which is why the publish job declares
`environment: pypi`. That environment now **has required reviewers** — `gaozhao1989`, with
`can_admins_bypass: false` — which turns "someone tagged it" into "someone approved it". See
"Who confirms what".

**2. The MCP Registry's GitHub namespace — nothing to do.** The registry derives the namespace
from the authentication method: GitHub authentication means the name **must** be
`io.github.<account>/...`, and this repository's `server.json` says
`io.github.gaozhao1989/pyjab-mcp`, which is the account that owns the repository. The
`registry` job's `mcp-publisher login github-oidc` establishes it on the first run, using the
OIDC token GitHub gives the job (`id-token: write`). There is no separate verification step to
perform in advance, and no secret to store.

The registry is in **preview**: its schema changes, and data may be reset. It has already
changed once here — see "What went wrong" below.

## Pre-flight

Releases are cut from `main`. **A version that has reached PyPI can never be withdrawn or
overwritten**, so everything that can fail is checked *before* the tag is pushed. These are
pyjab's pre-flight rules, copied deliberately: they were written after a release that shipped a
changelog the repository did not agree with, and one that used a local date a day ahead of its
tag.

Do not tag until each of these is what it should be.

**`main` is clean and pushed**, and `__version__` in `src/pyjab_mcp/__init__.py` is the new
version **in double quotes** — the release workflow reads it, and a single quote would make it
read something else.

**`CHANGELOG.md` has an entry for it, dated in UTC.** A local date can be a day ahead of the tag
for anyone east of UTC, and the date is part of what gets frozen below.

**The README carries the registry's ownership line.** The registry proves that whoever
publishes the metadata owns the PyPI package by looking for this exact line *in the published
package's README*:

```
mcp-name: io.github.gaozhao1989/pyjab-mcp
```

It has to be in the README **before** the tag. 0.1.0 published to PyPI and was refused by the
registry — `PyPI package 'pyjab-mcp' ownership validation failed` — and a published PyPI version
cannot have its metadata edited, so registering it needed a patch release. `tests/test_packaging.py`
checks the line against `server.json`'s name so the two cannot drift apart.

**The released sections are untouched:**

```console
python tools/check_changelog_immutable.py
```

**Nothing is already published under that number.** All three must come back empty or 404:

```console
curl -s -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/pyjab-mcp/VERSION/json
gh release view vVERSION
git tag -l vVERSION
```

**And the checks that run anywhere pass:**

```console
python -m pytest tests/
python tools/check_release_version.py vVERSION
python tools/check_pyjab_api_surface.py
python tools/check_documented_surface.py
python tools/check_local_only_files.py
python tools/check_changelog_immutable.py
```

The other guards need Windows, a JDK or the network, so CI is the authority for them; the L3 job
is the only one that can say a live window still works. It is dispatched by hand or runs weekly on
its own schedule, and the pipeline requires its result: no green `windows-gui.yml` run on the
tagged commit, no release — see
"What is deliberately not automated" below.

## Then

1. **Bump the version, write the changelog entry, and open a pull request.** The bump is
   reviewed like anything else, and **CI has to be green before it merges**.
2. **Dispatch L3 on the merged commit, and wait for it to be green.** It is the only layer that
   can say a live window still works, the job is `workflow_dispatch`-only, and the pipeline now
   requires a successful run on the commit being tagged:

   ```console
   gh workflow run windows-gui.yml -f java=17
   gh run watch
   ```
3. **Tag the merged commit and push the tag.** Annotated, with the version in the message — the
   tag is what the sdist was built from, and the message is what `git show` prints for it:

   ```console
   git tag -a v0.1.0 -m "pyjab-mcp 0.1.0"
   git push origin v0.1.0
   ```
4. **Approve the publish — the release's one human step.** After `verify` is green, the
   `publish to PyPI` job stops and waits. The review appears on that Actions run's page and
   GitHub emails the reviewer. Approving it resumes the run where it stopped: **nothing has to be
   re-tagged or re-run.** The `registry` job needs both `verify` and `pypi`, so it waits too.
5. **If the review is never approved, the release is half-published by construction.**
   `github-release` needs only `verify`, so it completes on its own: the GitHub release exists
   with the changelog as its notes while PyPI and the registry do not have the version. That is a
   delay, not a loss — approving the pending deployment later resumes the same run. **Rejecting**
   it is different: the run fails at that job, and the way forward is to re-run the failed jobs
   (a new tag would mean a new *version*, because the `v*` ruleset will not let this one be moved
   or deleted).

`release.yml` does the rest: it verifies that the tag matches `__version__`, `server.json` and the
changelog, runs the suite and the guards, builds and checks the artifact, publishes to PyPI through
Trusted Publishing, publishes the metadata to the MCP Registry, and creates the GitHub release
with the changelog section as its notes.

## After the tag

**Freeze the new section**, so that nothing can edit it later without CI saying so:

```console
python tools/check_changelog_immutable.py --record 0.1.0
```

It reads the section **out of the tag**, not the working copy, so what gets frozen is what
shipped. That is why it is a separate commit *after* the release: the tag has to exist first.
Commit the changed `tools/released_changelog_sections.json`.

**Then confirm what was actually published**, by installing it — the release workflow says the
upload succeeded, and this says the thing on PyPI is the thing that was built, tool surface and
all:

```console
python -m venv /tmp/check
/tmp/check/bin/python -m pip install "pyjab-mcp==0.1.2"
/tmp/check/bin/pyjab-mcp --version          # the version the wheel was built as
/tmp/check/bin/python -c "
import asyncio
from pyjab_mcp.server import create_server
print('tools:', [t.name for t in asyncio.run(create_server().list_tools())])
"
```

`--help` prints what the server needs and `--version` prints the version; both exit before the
transport starts, which is why they may write to stdout while the server may not. Until 0.1.2
the script ignored its arguments entirely, so `--help` printed nothing and exited 0 — a command
that looked like a check and was not one, which is why this step used to spell the version check
out the long way.

## If the tag is wrong

**A version that has reached PyPI is immutable.** Never move its tag: the tag is what the sdist
was built from, and pointing it somewhere else makes this repository describe a release that
never happened. The changelog guard exists for the same reason, and will fail if a released
section is edited.

If a tag was pushed but **nothing was published**, it can be moved — but `v*` tags are covered by
a repository ruleset, so that takes a deliberate unlock and re-lock, and it is not written down
here: a published how-to for disabling a protection is a how-to for disabling a protection.

If anything reached PyPI, cut a **patch** release instead. That is what PATCH is for.

## The version guard

`tools/check_release_version.py` refuses a release when the tag and the files disagree:

```console
$ python tools/check_release_version.py v0.1.0
CANNOT RELEASE v0.1.0:
  - src/pyjab_mcp/__init__.py says __version__ = '0.1.0.dev0', and the tag is '0.1.0'...
  - CHANGELOG.md has no section for '0.1.0'...
```

It is also what writes the release notes (`--notes`), so the notes and the check cannot
disagree about what a release contains. Both halves are pinned by `tests/test_guards.py`,
including the passing path.

## What went wrong the first time, and what now prevents it

The first draft of `server.json` followed the published JSON schema at
`.../schemas/2025-07-09/server.schema.json`, which spells the package fields
`registry_type`/`runtime_hint` and does not mention any other version. The registry's validator
then said:

```console
$ mcp-publisher validate
Error: validation failed: server returned status 422: ... "location":"body.packages[0].registryType" ...
⚠️  Deprecated schema detected: .../2025-07-09/server.schema.json
Expected current schema: .../2025-12-11/server.schema.json
```

Two lessons, both now mechanical:

* the **current** schema is `2025-12-11`, its fields are camelCase, and the registry treats a
  deprecated `$schema` as a failure to publish;
* the registry's own validator is the authority, not a schema file, and not memory. It runs in
  the `registry` job before `publish`, and it can be run locally or in the test suite with
  `PYJAB_MCP_REGISTRY=1` and `mcp-publisher` on `PATH`.

Had this been discovered during a real release, PyPI would already have had the package — which
cannot be undone.

## The registry schema moves, and how that is noticed early

A deprecated `$schema` is a **publish failure**, and the `registry` job runs after `pypi` — so
finding out during a release means finding out after PyPI already has the package.
`.github/workflows/registry-drift.yml` asks the registry's validator every Monday instead, on
a runner, with no tag involved. When it goes red the fix is two lines: `$schema` in
`server.json`, and the URL pinned in `tests/test_packaging.py`.

That workflow deliberately does **not** pin the `mcp-publisher` version it downloads — the
point is to ask what the registry currently accepts. The copy in `release.yml` is the same
download for the same reason.

## Discovery: what this relies on, and what it skips

**Installation never depended on a directory.** Two channels, both verified on 2026-10-10:

| channel | command | verified how |
|---|---|---|
| **PyPI** | `pip install pyjab-mcp` — and `uvx pyjab-mcp` for a client that takes a command | a clean venv installed `0.1.1` and the console script answered `--version`. `uvx` itself was **not** exercised (no `uv` on the machine that checked); it is a wrapper over the same install. |
| **GitHub** | `pip install "git+https://github.com/gaozhao1989/pyjab-mcp"` | a clean venv installed from `main` and reported `0.1.1` with all 15 tools registered. |

Both are in `README.md`, which is also what PyPI renders as the project page, and `docs/CLIENTS.md`
gives the per-client configuration.

**Aggregator directories are deliberately not a release step.** They were a criterion in the plan
and were removed on 2026-10-10, for reasons that were measured rather than assumed:

* they are **downstream consumers**: the Registry's own documentation says they *scrape its
  read-only REST API* "on a regular but infrequent basis (e.g., once per hour)". The registry
  entry is live (`io.github.gaozhao1989/pyjab-mcp`, `status: active`), so the one channel that
  costs nothing is already open — and a site that becomes useful later will pick the server up
  **without any action here**;
* of the four the plan named, one was **paused** ("New server submissions and listing changes are
  still paused"), one is **not a form at all** (Smithery publishes a public Streamable HTTP URL —
  blocked by this project's deliberate stdio-only decision — or a local MCPB bundle), and two ask
  for an account or a third-party issue;
* what they would add is *browsing discovery* for a Windows-only, JAB-dependent tool whose users
  arrive knowing what they want to automate. The cost — an issue here, an account there, a bundle
  to build — is real and the benefit is speculative.

If someone does want to revisit it, this is the state as measured on 2026-10-10:

| site | what it asks for |
|---|---|
| LobeHub | an issue in [`lobehub/lobehub`](https://github.com/lobehub/lobehub/issues) titled `[Server Submission]: <name>`; its template asks for the **official Registry name**, transport and licence |
| PulseMCP | nothing right now — submissions paused, reopening unannounced; it crawls the registry itself |
| Smithery | a public **Streamable HTTP** endpoint, or an **MCPB bundle** for local use |
| mcp.so | a `/submit` form and an account |

Anything submitted should copy the description from `server.json` verbatim — it is already written
to the registry's rules — and should state the two facts a reviewer asks for: Windows with Java
Access Bridge enabled, and that the server attaches to an application that is already running.

## What is deliberately not automated

* **Choosing the version.** Semantic versioning is a judgement about what changed.
* **Aggregator listings** (LobeHub, PulseMCP, Smithery, mcp.so). They pull from the official
  registry; a submission is a form on somebody else's site.
* **Dispatching L3.** `tests/e2e` and the `windows-gui.yml` job are the only things that can
  prove a live window still works, and the job is `workflow_dispatch`-only — it runs when
  somebody asks, and never by itself. The pipeline then *requires* the result: `verify` fails when
  the tagged commit has no successful `windows-gui.yml` run, so a forgotten dispatch is a refused
  release rather than an unverified one:

  ```console
  gh workflow run windows-gui.yml -f java=17
  gh run watch
  ```

* **Approving the publish.** Whether this version ships is a judgement no rule can make, so it is
  the one step a person performs — and the `pypi` environment's required review is where they
  perform it. No agent can click it, by design. See "Who confirms what".
