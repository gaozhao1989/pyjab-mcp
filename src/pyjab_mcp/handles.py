"""Handles: what an agent names an element by, and what makes that safe.

D2's decision, implemented
--------------------------

An agent sees a snapshot naming elements and later calls a tool with one of those names.
The obvious implementation is a table of live JAB references, and it is the wrong one:
pyjab's currency is a reference that must be released exactly once, and **every leak found
in pyjab so far came from holding a reference longer than the operation that took it.**

So a handle here is an opaque id mapped to ``{session, window, locator, expected identity}``
and **nothing is held between calls**. On use, the locator is resolved again, the result is
compared against what the snapshot recorded, and the reference is released immediately.

Three consequences worth stating, because each is a place this could have gone wrong:

* **Eviction releases nothing.** There is nothing to release -- which is the entire argument
  for this design rather than a table of references. The capacity bound is therefore about
  the size of a dict, not about correctness.
* **No TTL is needed for the leak reason the plan gave.** PLAN §3.2 wanted a 30-minute
  session TTL to stop handles leaking after an agent crashed; with no references held, a
  leaked handle costs a dict entry that the capacity bound evicts. The plan predates D2, and
  this is the amendment.
* **A handle that cannot be expressed as a locator gets no handle at all.** An element whose
  ``index_in_parent`` is unknown (JAB reports ``-1``) cannot be re-found reliably, and D2 is
  explicit that it must not get one that might act on the wrong node.

Verification is the part that turns "the tree moved and we clicked the wrong thing" into a
message, so it compares what D2 says to compare -- role, accessible name, index among
siblings, and bounds -- and treats **any** disagreement as a failure. A dynamic label that
renames itself will therefore fail verification and need a fresh snapshot. That is the
intended trade: re-snapshotting is cheap, and acting on the wrong node is not.
"""

from __future__ import annotations

import secrets
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .tree import Node, Record, Walk

#: How many handles are kept.  PLAN §3.2's number, and still the right shape of bound: the
#: table is per server, and a model that has walked away from 256 elements is not coming
#: back to the first one.
DEFAULT_CAPACITY = 256


@dataclass(frozen=True)
class HandleEntry:
    """Everything needed to find the element again, and to check it is still it."""

    handle: str
    session_id: str
    window_id: str
    locator: str
    expected: Record


#: Roles whose accessible name **is their content**, so it changes when the content does.
#:
#: ``JTextField("1122233455")`` with no ``setName`` reports "1122233455" as its name, because
#: Swing's ``AccessibleJTextComponent.getName()`` falls back to the text. A strict name check
#: therefore makes an edit invalidate the very handle that made it: set the field to "hello"
#: and the handle can no longer verify, so nothing can read back what it just wrote.
#:
#: The list is deliberately short and evidence-based: the roles where a name change is
#: explained by a content change. For everything else the name is a label, and a label that
#: changed at the same position is a different control -- which is the case D2 exists for.
NAME_IS_CONTENT_ROLES = frozenset({
    "text",
    "password text",
    "label",
    "table cell",
})


@dataclass(frozen=True)
class Verification:
    """The outcome of comparing a re-resolved element against what was recorded.

    ``problems`` block; ``warnings`` do not. The split exists because D2 asked for the
    accessible name to be compared, and a real application showed why that cannot be the
    whole rule for every role: for a text field the name *is* the value, so a name change is
    evidence of the edit rather than of a different element. Structural identity -- role,
    position among siblings, bounds -- is what blocks, and it is what catches the case the
    check is for: something else having moved into the position.
    """

    ok: bool
    problems: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()

    def message(self) -> str:
        if not self.ok:
            return (
                "the element has changed since the snapshot, so this handle was not used: "
                + "; ".join(self.problems)
                + ". Take a fresh snapshot."
            )
        if self.warnings:
            return (
                "the element's identity matches, with a note: "
                + "; ".join(self.warnings)
                + ". The structural identity (role, position, bounds) is unchanged, so this "
                "is the same element with different content."
            )
        return "the element matches what the snapshot recorded"


def locator_for(walk: Walk, index: int) -> Optional[str]:
    """The XPath that re-finds ``walk.nodes[index]``, or None when it cannot be expressed.

    Built as ``//role[@indexinparent=n]/role[@indexinparent=n]`` from the window down.

    ``indexinparent`` rather than ``[n]`` deliberately.  ``[n]`` counts a node's position
    among siblings *sharing its role*, which pyjab's own docs are explicit about -- and
    computing it from a walk means trusting that the walk saw every same-role sibling
    before this node.  A truncated walk has not, so a locator built that way would be
    quietly wrong exactly when the tree is large.  ``index_in_parent`` comes from the node
    itself and does not depend on the walk being complete.

    Returns None when any step's ``index_in_parent`` is negative: JAB uses ``-1`` for
    "unknown", and a locator containing it would match an arbitrary element.
    """
    nodes = walk.nodes
    if index < 0 or index >= len(nodes):
        return None
    chain: List[Node] = []
    cursor: Optional[int] = index
    while cursor is not None:
        node = nodes[cursor]
        chain.append(node)
        cursor = node.parent
    chain.reverse()

    steps: List[str] = []
    for node in chain:
        record = node.record
        if not record.role or record.index_in_parent < 0:
            return None
        steps.append(f"{record.role}[@indexinparent={record.index_in_parent}]")
    return "//" + "/".join(steps)


def locator_candidates(record: Record) -> "Tuple[str, ...]":
    """Locators that should find *record*, most survivable first.

    Ordered by what survives a change in the application, which is pyjab's own ordering for
    the same problem in ``inspector.suggest_locators``: a name is stable, an index is not, so
    the index appears only as a fallback and only alongside the name when there is one.

    This exists for elements that were *found* rather than *walked*: a walk knows the path
    from the window and :func:`locator_for` can write it out, while a search result knows only
    what it is. The caller resolves each candidate and keeps the first that comes back the
    same element, which is the disambiguation D2 asks for -- and an element none of them can
    find is one this project refuses to hand out a handle for.
    """
    role = record.role
    candidates: "List[str]" = []

    # A description is usable on its own, not only as a tie-breaker next to a name: in an
    # accessibility tree an unnamed control with a description is an ordinary thing, and it
    # is addressable. (pyjab's `suggest_locators` offers the description only alongside a
    # name; this is a superset, which is safe because every candidate is resolved and checked
    # before it becomes a handle.)
    try:
        by_name = f"[@name={_quote(record.name)}]" if record.name else ""
        by_description = (
            f"[@description={_quote(record.description)}]" if record.description else ""
        )
    except ValueError:
        # The name holds both kinds of quote and cannot be an xpath literal. Skipping those
        # candidates leaves the index ones, which is better than raising: the element is still
        # addressable, just less specifically.
        by_name = by_description = ""

    if by_name:
        candidates.append(f"//{role}{by_name}")
        if by_description:
            candidates.append(f"//{role}{by_name}{by_description}")
        prefix = f"//{role}{by_name}"
    elif by_description:
        candidates.append(f"//{role}{by_description}")
        prefix = f"//{role}{by_description}"
    else:
        prefix = f"//{role}"

    if record.index_in_parent >= 0:
        candidates.append(f"{prefix}[@indexinparent={record.index_in_parent}]")
    if not candidates and role:
        candidates.append(f"//{role}")
    return tuple(candidates)


def _quote(value: str) -> str:
    """Quote for an xpath literal, picking the quote the value does not contain.

    A name holding both kinds of quote cannot be expressed as a literal at all, and saying so
    is better than emitting something that parses as a different locator.
    """
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    raise ValueError(f"the name contains both quote characters: {value!r}")


def verify(expected: Record, actual: Record) -> Verification:
    """Compare a re-resolved element against the record the snapshot stored.

    Blocking: role, position among siblings, and bounds when both sides report them.
    Reported but not blocking: the accessible name, for the roles whose name *is* their
    content (see :data:`NAME_IS_CONTENT_ROLES`) -- and for every other role a name change
    still blocks, because a renamed button at the same position is a different button.

    This is D2 with one amendment, and the amendment came from a real run: L3 set a text
    field and could no longer read it back, because the edit had renamed it.
    """
    problems: List[str] = []
    warnings: List[str] = []

    if expected.role != actual.role:
        problems.append(f"role was {expected.role!r}, now {actual.role!r}")
    if expected.name != actual.name:
        change = f"name was {expected.name!r}, now {actual.name!r}"
        if expected.role.strip().lower() in NAME_IS_CONTENT_ROLES:
            warnings.append(change + f" (a {expected.role!r} reports its content as its name)")
        else:
            problems.append(change)
    if expected.index_in_parent != actual.index_in_parent:
        problems.append(
            f"index among siblings was {expected.index_in_parent}, "
            f"now {actual.index_in_parent}"
        )
    if expected.bounds and actual.bounds:
        before = tuple(expected.bounds.get(key) for key in ("x", "y", "width", "height"))
        now = tuple(actual.bounds.get(key) for key in ("x", "y", "width", "height"))
        if before != now:
            problems.append(f"bounds were {before}, now {now}")
    return Verification(ok=not problems, problems=tuple(problems), warnings=tuple(warnings))


class HandleTable:
    """Bounded, LRU, and holding no JAB references at all.

    Args:
        capacity: How many handles to keep before the least recently used is dropped.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._entries: "OrderedDict[str, HandleEntry]" = OrderedDict()

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, handle: object) -> bool:
        return handle in self._entries

    def add(self, *, session_id: str, window_id: str, locator: str,
            expected: Record) -> str:
        """Record one element and return its handle."""
        handle = self._new_handle()
        self._entries[handle] = HandleEntry(
            handle=handle, session_id=session_id, window_id=window_id,
            locator=locator, expected=expected,
        )
        # Eviction drops a dict entry and nothing else: no reference is held, so there is
        # nothing to release. See the module docstring.
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)
        return handle

    def get(self, handle: str) -> Optional[HandleEntry]:
        """The entry for *handle*, marking it as recently used."""
        entry = self._entries.get(handle)
        if entry is None:
            return None
        self._entries.move_to_end(handle)
        return entry

    def handles_for(self, session_id: str) -> Tuple[str, ...]:
        return tuple(
            handle for handle, entry in self._entries.items()
            if entry.session_id == session_id
        )

    def clear(self, session_id: Optional[str] = None) -> int:
        """Forget every handle, or every handle of one session.  Returns how many went."""
        if session_id is None:
            count = len(self._entries)
            self._entries.clear()
            return count
        doomed = [
            handle for handle, entry in self._entries.items()
            if entry.session_id == session_id
        ]
        for handle in doomed:
            del self._entries[handle]
        return len(doomed)

    def _new_handle(self) -> str:
        while True:
            handle = "e:" + secrets.token_hex(2)
            if handle not in self._entries:
                return handle


def locators_for_walk(walk: Walk, indices: Iterable[int]) -> "Dict[int, Optional[str]]":
    """Locators for the rendered nodes, so the caller can see which got no handle.

    Returning ``None`` for an element rather than a locator it cannot honour is the point:
    the snapshot can then tell the agent that this node cannot be acted on, instead of
    handing it a name that resolves to something arbitrary.
    """
    return {index: locator_for(walk, index) for index in indices}
