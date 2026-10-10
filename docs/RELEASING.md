# Releasing pyjab-mcp

A release is: **bump the version, move the changelog, tag, and let
[`.github/workflows/release.yml`](../.github/workflows/release.yml) do the rest.** The workflow
is tag-driven, so a tag is the only thing that publishes anything.

Released versions are immutable — PyPI will not let a version be replaced or removed
(`AGENTS.md` §2.3). That is why every check runs **before** the first upload, and why the
version guard exists.

## One-time setup

Two things have to exist before the first tag. Only the first needs a person.

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
`environment: pypi`. The same environment can be given required reviewers, which turns "someone
tagged it" into "someone approved it" without changing anything here.

**2. The MCP Registry's GitHub namespace — nothing to do.** The registry derives the namespace
from the authentication method: GitHub authentication means the name **must** be
`io.github.<account>/...`, and this repository's `server.json` says
`io.github.gaozhao1989/pyjab-mcp`, which is the account that owns the repository. The
`registry` job's `mcp-publisher login github-oidc` establishes it on the first run, using the
OIDC token GitHub gives the job (`id-token: write`). There is no separate verification step to
perform in advance, and no secret to store.

The registry is in **preview**: its schema changes, and data may be reset. It has already
changed once here — see "What went wrong" below.

## Cutting a release

```console
# 1. The version, in one place (everything else reads it).
$EDITOR src/pyjab_mcp/__init__.py        # __version__ = "0.1.0", not "0.1.0.dev0"

# 2. Move what is under Unreleased into a section named after the release.
$EDITOR CHANGELOG.md                     # ## [0.1.0] - 2026-10-10

# 3. server.json, which the registry reads.
$EDITOR server.json                      # "version": "0.1.0", in both places

# 4. Check, before anything is tagged.
python tools/check_release_version.py v0.1.0

# 5. Commit on main, then tag the commit that has all of the above.
git commit -am "release: 0.1.0"
git tag v0.1.0
git push origin main --tags
```

The tag starts the workflow:

| job | what it does | fails when |
|---|---|---|
| `verify` | the version guard, the portable suite, every guard, `python -m build`, the dist-content guard, `twine check` | anything disagrees, or the artifact is wrong |
| `pypi` | `pypa/gh-action-pypi-publish`, OIDC, environment `pypi` | the version already exists on PyPI (immutability), or the publisher is not configured |
| `registry` | checks `server.json` with the registry's **own validator**, then `mcp-publisher login github-oidc` and `publish` | the registry rejects the metadata — after PyPI has the package, which is why the validator runs first |
| `github-release` | creates the GitHub release, with the changelog section as its notes | there is no changelog section for the tag |

To rehearse without publishing, dispatch the workflow by hand:

```console
gh workflow run release.yml
```

A manual run has no tag, so `verify` checks whatever the tree claims to be and *warns* rather
than fails on a `.devN` version — a dry run is allowed to find the release unprepared. The
three publishing jobs are skipped unless the event is a tag.

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

## What is deliberately not automated

* **Choosing the version.** Semantic versioning is a judgement about what changed.
* **Aggregator listings** (LobeHub, PulseMCP, Smithery, mcp.so). They pull from the official
  registry; a submission is a form on somebody else's site.
* **The decision to publish.** `tests/e2e` and the `windows-gui.yml` job are the only things
  that can prove a live window still works, and `windows-gui.yml` is dispatched by hand. A
  release should follow a green one:

  ```console
  gh workflow run windows-gui.yml -f java=17
  gh run watch
  ```
