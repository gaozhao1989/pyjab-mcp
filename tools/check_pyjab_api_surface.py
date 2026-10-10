#!/usr/bin/env python
"""Every pyjab name this package touches, and what gives us the right to touch it.

Why this exists
---------------

The working agreement has two rules that are easy to keep by hand right up until someone is
in a hurry, and both have already been tested once in this project's short life:

* **the only interface to pyjab is its published API** (AGENTS.md §1.1). Reaching into a
  private name is always the shortest path when something is in the way, and there is no
  moment at which it *looks* wrong — it works, on the developer's machine, until a pyjab
  release changes it.
* **nothing may depend on an unreleased pyjab change** (AGENTS.md §1.2). The sibling pyjab
  checkout is 62 commits past the `v1.8.0` tag, so `JABElement.walk`, `pyjab.inspector` and
  `java_process_image_paths` all exist and none of them is in the released wheel. Reading
  the checkout is how you conclude "pyjab has it"; installing the wheel is how you find out
  it does not.

Why a registry rather than a rule
---------------------------------

"What is public?" is a judgement about somebody else's project, and a checker cannot decide
it. What a checker *can* do is refuse to let a new touchpoint appear without somebody
writing that judgement down — which is exactly the failure that happens: one line added
while fixing something else, and a dependency nobody chose.

The failure mode this prevents is also the one the M1 precheck hit: a name that exists in a
checkout, is imported, and does not exist in any released version. Rule 4 below makes that
a build failure rather than a surprise on a user's machine.

How the set is derived
----------------------

From this package's own source, with ``ast``. Every ``import pyjab...``,
``from pyjab... import X``, every ``getattr``/``hasattr``/``find_spec`` probe naming a pyjab
path, and every attribute access on something that came from pyjab (``Win32Utils().pump_messages()``,
``driver.walk()``) is a touchpoint. The derivation is deliberately shallow — a name resolved
through two assignments would be missed — but every miss is a touchpoint that is also
missing from the registry, so it fails the next time somebody looks, and the registry is
the part that is enforced.

Usage
-----

    python tools/check_pyjab_api_surface.py            # check
    python tools/check_pyjab_api_surface.py --derived   # what the source says, as registry rows
"""

from __future__ import annotations

import ast
import importlib.metadata
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "src" / "pyjab_mcp"

#: A touchpoint: ``(dotted pyjab path, symbol)``.  The symbol is ``"*"`` for a module
#: import, a bare name for an import or a probe, and ``Class.attribute`` for an attribute
#: access on something pyjab handed us.
Touchpoint = Tuple[str, str]

PUBLIC = "public"
UNRELEASED = "unreleased"


@dataclass(frozen=True)
class Entry:
    """What gives us the right to touch this name.

    Attributes:
        status: ``public`` — present and documented in a released pyjab; or
            ``unreleased`` — exists in the sibling checkout only, and may therefore be
            *probed* but never *imported*.
        released_in: The pyjab version that first shipped it. Required for ``public``: the
            whole point is that "it is in pyjab" is not an answer, "it is in pyjab
            1.8.0" is.
        note: Where it is documented, or what we are waiting for.
    """

    status: str
    released_in: str
    note: str


#: ``(path, symbol) -> Entry``.
#:
#: ``released_in`` was verified rather than remembered, because that distinction is the
#: whole reason this file exists. The 1.9.0 entries were checked against the installed
#: wheel, and the 1.8.0 entries against the 1.8.0 sdist (``pip download pyjab==1.8.0
#: --no-binary :all:``), after the precheck found the sibling checkout 62 commits ahead of
#: every release and this project quoting APIs from it.
#:
#: A symbol with ``()`` in it is the *result* of a method call:
#: ``JABDriver.find_element_by_xpath().as_record`` is the record of the element that method
#: returned. Constructors are not marked, because an instance has the class's attributes.
SURFACE: Dict[Touchpoint, Entry] = {
    ("pyjab", "*"): Entry(
        PUBLIC, "1.8.0",
        "The package itself, imported inside functions only (it is Windows-only at import "
        "time). Used for capability probes; see tests/test_packaging.py for the "
        "module-scope rule.",
    ),
    ("pyjab", "list_java_windows"): Entry(
        UNRELEASED, "",
        "Probed with getattr/hasattr, never imported: the public window listing this "
        "project asked for (backend.REQUIREMENTS key 'list_java_windows', AGENTS.md 6.2 "
        "R1). 1.9.0 did not add it. The day it appears, PyjabBackend starts using it with "
        "no change here.",
    ),
    ("pyjab.common.win32utils", "*"): Entry(
        PUBLIC, "1.8.0",
        "Imported inside open_backend to prove the Windows modules are reachable before the "
        "bridge thread reports itself up; the import itself has no side effect.",
    ),
    ("pyjab.common.win32utils", "Win32Utils"): Entry(
        PUBLIC, "1.8.0",
        "The class whose pump is driven on the bridge thread.",
    ),
    ("pyjab.common.win32utils", "Win32Utils.pump_messages"): Entry(
        PUBLIC, "1.8.0",
        "Public and documented: its own docstring states that COM events are delivered to "
        "the thread that called Windows_run() and that this thread must service the queue. "
        "Non-blocking and cheap when the queue is empty. Called only from BridgeThread's "
        "owning thread -- see .agents/skills/pyjab-mcp-bridge-threading/SKILL.md.",
    ),
    ("pyjab.config", "*"): Entry(
        PUBLIC, "1.8.0",
        "pyjab's platform-independent configuration module: DLL discovery, poll intervals, "
        "and the Java Access Bridge properties file. Imported for diagnostics.",
    ),
    ("pyjab.config", "A11Y_PROPS_PATH"): Entry(
        PUBLIC, "1.8.0",
        "Probed with getattr. The file pyjab writes to enable Java Access Bridge. Read, "
        "never written: constructing pyjab's Service() would write it, and a diagnostics "
        "tool that changes what it reports on is worse than none.",
    ),
    ("pyjab.config", "A11Y_PROPS_CONTENT"): Entry(
        PUBLIC, "1.8.0",
        "Probed with getattr. The exact content Service() writes, used to decide whether "
        "the file already enables JAB -- compared in full, because a file that merely "
        "mentions AccessBridge may also configure other assistive technologies.",
    ),
    ("pyjab.config", "find_bridge_dll"): Entry(
        PUBLIC, "1.8.0",
        "Probed with getattr, then called: pure location lookup, no load and no side effect, "
        "documented in pyjab/config.py as useful for diagnostics.",
    ),
    ("pyjab.config", "describe_bridge_dll_search"): Entry(
        PUBLIC, "1.8.0",
        "Probed with getattr, then called: the human-readable list of where the DLL search "
        "looked, which is what makes a missing DLL actionable.",
    ),
    ("pyjab.inspector", "*"): Entry(
        PUBLIC, "1.9.0",
        "Probed with importlib.util.find_spec only -- this module is never imported. It is "
        "reached as a *command*, `python -m pyjab.inspector windows --json`, which is what "
        "docs/3-pyjab.md documents and what the wheel installs. Using the published CLI is "
        "the point: its bridge_without_window() helper is what R1 needs and is not in the "
        "module's __all__.",
    ),
    ("pyjab.jabdriver", "JABDriver"): Entry(
        PUBLIC, "1.8.0",
        "Constructed to bind a window, and that construction is also what arms the bridge: "
        "it loads the DLL, calls Windows_run() on the calling thread and registers the JAB "
        "prototypes. Imported inside two functions; never used as a context manager, "
        "because __exit__ terminates the bound process by pid (AGENTS.md 6.2 R2).",
    ),
    ("pyjab.jabdriver", "JABDriver.find_element_by_xpath"): Entry(
        PUBLIC, "1.8.0",
        "How a handle is re-resolved (D2). The returned JABElement is owned by this layer "
        "and is released in a finally -- see the next entry.",
    ),
    ("pyjab.jabdriver", "JABDriver.find_element_by_xpath() -> as_record"): Entry(
        PUBLIC, "1.9.0",
        "The record of the re-resolved element, compared against the identity stored with "
        "the handle. as_record() is new in 1.9.0 and pyjab documents it as exactly this: "
        "the fields a locator matches on and a later check verifies.",
    ),
    ("pyjab.jabdriver", "JABDriver.hwnd"): Entry(
        PUBLIC, "1.8.0",
        "Documented property; reported to the agent so a session can be matched against "
        "java_list_windows.",
    ),
    ("pyjab.jabdriver", "JABDriver.pid"): Entry(
        PUBLIC, "1.8.0",
        "Documented property; reported, and never used to signal anything -- __exit__ is "
        "the only thing in pyjab that acts on the pid, and this project does not call it.",
    ),
    ("pyjab.jabdriver", "JABDriver.find_element_by_xpath() -> release_jabelement"): Entry(
        PUBLIC, "1.8.0",
        "The ownership rule: release every object JAB hands out, exactly once. It lives on "
        "**JABElement**, not on JABDriver -- and this line was written as "
        "`driver.release_jabelement(element)` until the contract test ran on a real Windows "
        "runner and reported that JABDriver has no such attribute. The L1 fake was shaped "
        "like the mistake, so it agreed; tests/_fakedriver.py now mirrors pyjab, where the "
        "element gives its own reference back.",
    ),
    ("pyjab.jabdriver", "JABDriver.root_element"): Entry(
        PUBLIC, "1.8.0",
        "The window's own element, and the root a snapshot walks from. Owned by the driver; "
        "not released by this project.",
    ),
    ("pyjab.jabdriver", "JABDriver.root_element.as_record"): Entry(
        PUBLIC, "1.9.0",
        "The window's own record, rendered as the first line of a snapshot.",
    ),
    ("pyjab.jabdriver", "JABDriver.root_element.walk"): Entry(
        PUBLIC, "1.9.0",
        "The bounded walk (D1): yields (depth, record) and releases every JAB reference as "
        "it advances, which is why a snapshot holds none. 1.9.0 is the first release with "
        "it -- the sibling checkout had it for 62 commits first, which is the trap this "
        "file's released_in field exists for.",
    ),
    ("pyjab.jabdriver", "JABDriver.root_element.walk() -> limit_hit"): Entry(
        PUBLIC, "1.9.0",
        "Probed with getattr on the JABTree, because the attribute is read defensively. It "
        "is the walk's own record of having hit its limit; this project also tracks its own "
        "early stop, since stopping the iteration is not the same as pyjab stopping it.",
    ),
    ("pyjab.jabdriver", "JABDriver.find_element_by_xpath() -> text"): Entry(
        PUBLIC, "1.8.0",
        "The only public text API: a property returning the whole string, None when the "
        "element has no Accessible Text interface, and \"\" when it has one and is empty "
        "(pyjab changed it from raising, which turned 'the field is now empty' into a "
        "RuntimeError). There is no public range read, so java_get_text fetches the whole "
        "value and slices what it returns rather than reaching for the private helpers in "
        "common/textreader.py.",
    ),
    ("pyjab.jabdriver", "JABDriver.find_element_by_xpath() -> table"): Entry(
        PUBLIC, "1.8.0",
        "The table's own counts: row_count and column_count, plus header and selection "
        "counts. Documented in pyjab's Tables section ('info = table.table'), and it is where "
        "a page's size comes from -- M4 pages because a Java table is exactly the thing that "
        "must never be returned whole. Header *text* is not exposed by any public property, "
        "so this tool reports counts and cell values only.",
    ),
    ("pyjab.jabdriver", "JABDriver.find_element_by_xpath() -> get_cell"): Entry(
        PUBLIC, "1.8.0",
        "Reads one cell from the table's cell list -- good for text, and the docs are "
        "explicit that many tables report those cells with bounds of -1, which is why "
        "selecting goes through the accessible selection API instead (M5). The cell it "
        "returns is owned by the caller, so table_driver releases every one.",
    ),
    ("pyjab.jabdriver", "JABDriver.find_element_by_xpath() -> get_cell() -> text"): Entry(
        PUBLIC, "1.8.0",
        "The cell's text. None means the cell has no Accessible Text interface and \"\" means "
        "it is text and empty; table_driver keeps the two apart all the way to the reply, "
        "because collapsing them would report a missing interface as an empty cell.",
    ),
    ("pyjab.jabdriver", "JABDriver.find_element_by_xpath() -> get_cell() -> release_jabelement"): Entry(
        PUBLIC, "1.8.0",
        "Every cell the table hands out is released, one at a time, in a finally. A 200-row "
        "table read without that is 200 Java objects left behind, which is issue #43's shape "
        "at exactly the scale that makes it hurt.",
    ),
    ("pyjab.jabdriver", "JABDriver.find_elements_by_name"): Entry(
        PUBLIC, "1.8.0",
        "Search by exact accessible name. Returns elements the caller owns, so find_driver "
        "releases every one of them -- including the ones past its result limit, which is the "
        "leak a 'break' would leave.",
    ),
    ("pyjab.jabdriver", "JABDriver.find_elements_by_role"): Entry(
        PUBLIC, "1.8.0",
        "Search by role, as pyjab's role names spell it ('push button', 'text', ...).",
    ),
    ("pyjab.jabdriver", "JABDriver.find_elements_by_description"): Entry(
        PUBLIC, "1.8.0",
        "Search by accessible description, for controls whose name is empty.",
    ),
    ("pyjab.jabdriver", "JABDriver.find_elements_by_states"): Entry(
        PUBLIC, "1.8.0",
        "Search by state list; takes a list or a comma-joined string.",
    ),
    ("pyjab.jabdriver", "JABDriver.find_elements_by_xpath"): Entry(
        PUBLIC, "1.8.0",
        "Search by locator, which is also what java_find's xpath selector uses.",
    ),
    ("pyjab.jabdriver", "JABDriver.title"): Entry(
        PUBLIC, "1.8.0",
        "Documented property; reported with the session.",
    ),
    ("pyjab.jabelement", "JABElement"): Entry(
        PUBLIC, "1.8.0",
        "Imported inside a probe, inside a try/except ImportError: the module is "
        "Windows-only, and whether it has walk/as_record is genuinely unknown off Windows.",
    ),
    ("pyjab.jabelement", "JABElement.walk"): Entry(
        PUBLIC, "1.9.0",
        "Probed with hasattr. The tree-walk API D1 asked for: plain dicts, released "
        "references, and a JABTree that can say whether it was cut short.",
    ),
    ("pyjab.jabelement", "JABElement.as_record"): Entry(
        PUBLIC, "1.9.0",
        "Probed with hasattr, alongside walk: a walk without as_record would give records "
        "but no way to re-verify a handle, so both are required together.",
    ),
}

#: Parameters that hold a pyjab object, by ``(relative file, function)``.
#:
#: The derivation follows local names, and an object that arrives as an *argument* has no
#: local origin to follow.  These are the functions where that matters -- they take a driver
#: rather than reaching for one so that the ownership rule inside them can be tested against
#: a reference-counting fake -- and without this table their touchpoints would either vanish
#: from the derivation or, worse, be picked up by accident from a same-named local in
#: another function.  Stating it makes the derivation independent of the order functions
#: happen to appear in.
DRIVER_PARAMETERS: Dict[str, Dict[str, Dict[str, Touchpoint]]] = {
    "src/pyjab_mcp/backend.py": {
        "walk_driver": {"driver": ("pyjab.jabdriver", "JABDriver")},
        "resolve_driver": {"driver": ("pyjab.jabdriver", "JABDriver")},
    },
}

#: Prefixes that mean "somebody is reaching into pyjab".  A registry entry may not carry
#: one of these, which is how rule 3 below is enforced: there is no way to record a private
#: touchpoint and pass.
PRIVATE_MARKERS = ("_",)


def is_pyjab(dotted: str) -> bool:
    """True for ``pyjab`` and ``pyjab.something``, false for ``pyjab_mcp``."""
    return dotted == "pyjab" or dotted.startswith("pyjab.")


def _attribute_point(module: str, symbol: str, attribute: str) -> Touchpoint:
    """``(module, symbol.attribute)``, with a call boundary marked as ``->``.

    ``JABDriver.find_element_by_xpath() -> release_jabelement`` says what it is: an attribute
    of whatever that call returned. Writing it with a dot instead
    (``...find_element_by_xpath().release_jabelement``) reads as though the driver had the
    method -- and that misreading is exactly the bug this file's registry is meant to catch,
    so the separator earns its place.
    """
    if symbol == "*":
        return (module, attribute)
    if symbol.endswith(")"):
        return (module, f"{symbol} -> {attribute}")
    return (module, f"{symbol}.{attribute}")


def _resolve_call_target(node: ast.AST, owners: Mapping[str, Touchpoint]) -> Optional[Touchpoint]:
    """The pyjab touchpoint a callable expression refers to, if any."""
    if isinstance(node, ast.Name):
        return owners.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _resolve_call_target(node.value, owners)
        if base is None:
            return None
        return _attribute_point(*base, node.attr)
    if isinstance(node, ast.Call):
        base = _resolve_call_target(node.func, owners)
        if base is None:
            return None
        module, symbol = base
        if "." not in symbol:
            # Calling a class gives an instance of that class, so the attributes that follow
            # are the same set the class documents: ``JABDriver()`` is a JABDriver, and
            # ``Win32Utils().pump_messages`` is ``Win32Utils.pump_messages``.
            return (module, symbol)
        # Calling a *method* gives a different object -- ``find_element_by_xpath(...)``
        # returns a JABElement -- so that boundary is marked rather than pretending the
        # driver has an ``as_record``.
        return (module, f"{symbol}()")
    return None


class _FileScan(ast.NodeVisitor):
    """Collect touchpoints in one module.

    Names are resolved through a flat map of *local name -> touchpoint* built from the
    file's imports and from assignments of the form ``name = PyjabClass(...)``.  Flat rather
    than scoped because the failure direction is safe: a name shadowed somewhere else can
    only produce *more* touchpoints, and an unrecorded touchpoint is a build failure.
    """

    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.owners: Dict[str, Touchpoint] = {}
        self.sites: Dict[Touchpoint, List[str]] = defaultdict(list)

    # -- bookkeeping ---------------------------------------------------------------

    def _record(self, point: Touchpoint, kind: str, lineno: int) -> None:
        self.sites[point].append(f"{kind} at {self.relative}:{lineno}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if not is_pyjab(alias.name):
                continue
            # The touchpoint is the module that is imported.  Without an alias the local
            # binding is the top-level package, which is what an attribute access below
            # resolves through -- so both are recorded.
            self._record((alias.name, "*"), "import", node.lineno)
            if alias.asname:
                self.owners[alias.asname] = (alias.name, "*")
            else:
                self.owners["pyjab"] = ("pyjab", "*")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if is_pyjab(module):
            for alias in node.names:
                local = alias.asname or alias.name
                self.owners[local] = (module, alias.name)
                self._record((module, alias.name), "import", node.lineno)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        seeded = DRIVER_PARAMETERS.get(self.relative, {}).get(node.name, {})
        saved = {name: self.owners.get(name) for name in seeded}
        self.owners.update(seeded)
        self.generic_visit(node)
        for name, previous in saved.items():
            if previous is None:
                self.owners.pop(name, None)
            else:
                self.owners[name] = previous

    def visit_Assign(self, node: ast.Assign) -> None:
        # `backend = PyjabBackend()` and `config = config_module` both hand a local name a
        # pyjab origin, and the attribute accesses and probes below resolve through it.
        target = _resolve_call_target(node.value, self.owners)
        if target is not None:
            for name in node.targets:
                if isinstance(name, ast.Name):
                    self.owners[name.id] = target
        self.generic_visit(node)

    # -- touchpoints ---------------------------------------------------------------

    def visit_Attribute(self, node: ast.Attribute) -> None:
        target = _resolve_call_target(node.value, self.owners)
        if target is not None and is_pyjab(target[0]):
            self._record(_attribute_point(*target, node.attr), "attribute", node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func

        # getattr(owner, "name") / hasattr(owner, "name")
        if (
            isinstance(func, ast.Name)
            and func.id in {"getattr", "hasattr"}
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            owner = self.owners.get(node.args[0].id)
            if owner is not None and is_pyjab(owner[0]):
                point = _attribute_point(*owner, node.args[1].value)
                self._record(point, f"{func.id} probe", node.lineno)

        # importlib.util.find_spec("pyjab.something")
        if isinstance(func, ast.Attribute) and func.attr == "find_spec" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and is_pyjab(arg.value):
                self._record((arg.value, "*"), "find_spec probe", node.lineno)

        # An attribute call on, or of, something pyjab handed us.
        if isinstance(func, ast.Attribute):
            target = _resolve_call_target(func.value, self.owners)
            if target is not None and is_pyjab(target[0]):
                self._record(_attribute_point(*target, func.attr), "attribute", node.lineno)

        self.generic_visit(node)


def derive_sites(package: Path = PACKAGE) -> Dict[Touchpoint, List[str]]:
    """Every pyjab touchpoint in *package*, with where it was seen."""
    found: Dict[Touchpoint, List[str]] = defaultdict(list)
    files = sorted(path for path in package.rglob("*.py") if "__pycache__" not in path.parts)
    if not files:
        raise SystemExit(f"no modules under {package}")
    for path in files:
        relative = path.relative_to(REPO_ROOT).as_posix()
        scan = _FileScan(relative)
        scan.visit(ast.parse(path.read_text(encoding="utf-8")))
        for point, where in scan.sites.items():
            found[point].extend(where)
    return dict(found)


def _installed_version() -> Optional[str]:
    try:
        return importlib.metadata.version("pyjab")
    except importlib.metadata.PackageNotFoundError:
        return None


def _version_tuple(text: str) -> Tuple[int, ...]:
    parts: List[int] = []
    for chunk in text.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def check(
    sites: Optional[Mapping[Touchpoint, Sequence[str]]] = None,
    surface: Optional[Mapping[Touchpoint, Entry]] = None,
    installed: Optional[str] = "auto",
) -> List[str]:
    """Return every problem, as sentences.  An empty list means the surface is accounted for."""
    sites = derive_sites() if sites is None else sites
    surface = SURFACE if surface is None else surface
    if installed == "auto":
        installed = _installed_version()

    problems: List[str] = []

    for point in sorted(set(sites) - set(surface)):
        where = ", ".join(sites[point])
        problems.append(
            f"UNRECORDED: {point[0]}.{point[1]} ({where}) -- add it to SURFACE in this "
            "script with its status, the released pyjab version that has it, and where it "
            "is documented."
        )

    for point in sorted(set(surface) - set(sites)):
        problems.append(
            f"STALE: {point[0]}.{point[1]} is recorded but no longer used -- project code "
            "must not keep a licence to touch a name it does not touch. Remove the entry, "
            "or find what stopped using it."
        )

    for point in sorted(set(sites) & set(surface)):
        entry = surface[point]
        kinds = {where.split(" ", 1)[0] for where in sites[point]}
        label = f"{point[0]}.{point[1]}"

        if entry.status not in {PUBLIC, UNRELEASED}:
            problems.append(
                f"PRIVATE: {label} is recorded as {entry.status!r}. There is no acceptable "
                "private status: ask pyjab for a public API instead (AGENTS.md 1.1)."
            )
            continue
        if any(name.startswith(PRIVATE_MARKERS) for name in point[1].split(".")):
            problems.append(
                f"PRIVATE: {label} has a private name. Reaching into pyjab is the one thing "
                "the separate repository exists to prevent -- see AGENTS.md 6.2."
            )
            continue
        if entry.status == PUBLIC and not entry.released_in:
            problems.append(
                f"NO VERSION: {label} is public but does not say which pyjab release has "
                "it. 'It is in pyjab' is not an answer; the checkout can be ahead of every "
                "release."
            )
        if entry.status == UNRELEASED and "import" in kinds:
            problems.append(
                f"UNRELEASED DEPENDENCY: {label} is imported, and it is recorded as "
                "unreleased. Probe it (hasattr/getattr) and report the gap, or wait for the "
                "release and bump the pin -- do not start a milestone on an unreleased "
                "change (AGENTS.md 1.2)."
            )
        if (
            entry.status == PUBLIC
            and installed
            and entry.released_in
            and "import" in kinds
            and _version_tuple(installed) < _version_tuple(entry.released_in)
        ):
            problems.append(
                f"NOT INSTALLED: {label} is imported but recorded as released in "
                f"{entry.released_in}, and pyjab {installed} is installed. Raise the pin in "
                "pyjab-mcp/pyproject.toml in the same change that starts using it."
            )

    return problems


def _print_derived(sites: Mapping[Touchpoint, Sequence[str]]) -> None:
    print("# paste into SURFACE, then record status/released_in/note by hand:")
    for point in sorted(sites):
        kinds = ", ".join(sorted(sites[point]))
        print(f'    ("{point[0]}", "{point[1]}"): Entry(PUBLIC, "1.8.0", ""),  # {kinds}')


def main(argv: Sequence[str] = ()) -> int:
    sites = derive_sites()
    if "--derived" in argv:
        _print_derived(sites)
        return 0

    print(f"{len(sites)} pyjab touchpoint(s) in src/pyjab_mcp/:")
    for point in sorted(sites):
        kinds = ", ".join(sorted(sites[point]))
        entry = SURFACE.get(point)
        marker = "  " if entry else "NEW "
        status = entry.status if entry else "unrecorded"
        print(f"  {marker}{point[0]}.{point[1]}  [{status}]  {kinds}")

    problems = check(sites=sites)
    if problems:
        print()
        for problem in problems:
            print(f"::error:: {problem}")
        print()
        print("FAILED: every pyjab name this package touches must be accounted for.")
        print("  See the module docstring: this is what keeps 'the only interface is the")
        print("  published API' true after the first person is in a hurry.")
        return 1

    print(
        f"\nPASSED: all {len(sites)} touchpoint(s) are public and released in the installed "
        "pyjab, or are probed rather than depended on."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
