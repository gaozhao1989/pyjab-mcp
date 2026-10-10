"""Tree records, and turning a bounded walk into text an agent can afford to read.

Two things live here, and both are pure: the **record** (a plain dict of the fields pyjab's
``as_record()`` hand out, turned into something with a type), and the **renderer** that
turns a bounded walk into one line per node with a handle.

Why the renderer is separate from the walk
------------------------------------------

This project's first constraint is that it never returns a whole tree: a Java window can
hold thousands of nodes, and the full tree is not slow so much as unusable -- it puts
hundreds of thousands of tokens into a model's context. So the view is bounded twice over:

* the walk itself is bounded (``max_depth`` and a raw record cap), and it is **stopped**,
  not truncated after the fact, so a big window costs a bounded number of bridge calls
  rather than all of them;
* the kept view is bounded (``max_nodes``).

And because a bounded view that does not say it was bounded is indistinguishable from a
window that genuinely has less in it, the result carries ``truncated`` plus the reason.
That is this project's rule about measurements, applied to a snapshot: the caller must be able to
tell "the window is like this" from "I stopped reading".

What "interactive only" means here
----------------------------------

Not a role allow-list -- a **container deny-list**. An allow-list drops whatever nobody
thought of, which in an accessibility tree is exactly the thing you were looking for; a
deny-list drops the pure layout scaffolding (root pane, panel, viewport, ...) and keeps
everything that could carry meaning. A named container is kept even if its role is on the
list, and so is any container that has a kept descendant, because a tree view with the
structure removed is not a tree view.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: Roles that exist only to arrange other things.  A node with one of these roles is
#: dropped when ``only_interactive`` is on -- unless it has a name (a named container
#: carries information: ``panel "Update"``) or a kept descendant.
CONTAINER_ROLES = frozenset({
    "root pane",
    "panel",
    "scroll pane",
    "viewport",
    "layered pane",
    "content pane",
    "glass pane",
    "split pane",
    "filler",
    "unknown",
})

#: The fields a caller may ask to see, beyond the role and name that are always rendered.
KNOWN_FIELDS = ("states", "bounds", "description", "children", "index", "depth")

DEFAULT_FIELDS = ("states",)

#: Estimated tokens per line, used only for the snapshot's own reporting.  The real
#: tokeniser is the client's, so this is a deliberately conservative character-based
#: estimate; the hard budget assertion in the tests uses the same number, and the default
#: budgets leave headroom for a label-heavy window. Staying under 4k tokens by default is
#: a release criterion, not a preference.
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Record:
    """One element's identity and geometry, as plain data.

    The fields are pyjab's ``as_record()`` fields, and they are exactly the ones D2's
    handle verification needs: role, name, index among siblings, children count, depth,
    states and bounds.  Plain data on purpose -- pyjab hands out a JAB reference with a
    lifetime, and a record cannot leak because it holds nothing.
    """

    role: str
    name: str
    index_in_parent: int
    children_count: int
    object_depth: int
    states: Tuple[str, ...] = ()
    description: str = ""
    bounds: Optional[Mapping[str, int]] = None

    @classmethod
    def from_pyjab(cls, raw: Mapping[str, Any]) -> "Record":
        """Build a record from pyjab's ``as_record()`` dict.

        Missing keys are tolerated and defaulted rather than raising: a bridge that answers
        with a partial record is a fact about the target application, not a reason to fail
        the whole snapshot.
        """
        raw_states = raw.get("states") or ""
        if isinstance(raw_states, str):
            states = tuple(part for part in (s.strip() for s in raw_states.split(",")) if part)
        else:
            states = tuple(str(part) for part in raw_states)
        bounds = raw.get("bounds") or None
        return cls(
            role=str(raw.get("role") or ""),
            name=str(raw.get("name") or ""),
            index_in_parent=int(raw.get("index_in_parent") or 0),
            children_count=int(raw.get("children_count") or 0),
            object_depth=int(raw.get("object_depth") or 0),
            states=states,
            description=str(raw.get("description") or ""),
            bounds=dict(bounds) if bounds else None,
        )

    @property
    def label(self) -> str:
        """``role "name"``, the way every pyjab tool prints an element."""
        return f'{self.role} "{self.name}"' if self.name else (self.role or "unknown")

    def identity(self) -> Tuple[Any, ...]:
        """The tuple a handle verifies against.  Bounds are compared separately."""
        return (self.role, self.name, self.index_in_parent)


@dataclass(frozen=True)
class Node:
    """A record plus where it sits in the walked tree."""

    record: Record
    depth: int
    parent: Optional[int] = None
    #: 1-based position among siblings sharing this node's role.  This is what pyjab's
    #: ``[n]`` XPath position counts, so it is what a locator needs -- ``index_in_parent``
    #: counts all siblings, which is a different question.
    role_position: int = 1


@dataclass(frozen=True)
class Walk:
    """A bounded walk: the root, the nodes below it, and how it was cut short."""

    root: Record
    nodes: Tuple[Node, ...] = ()
    #: True when a limit stopped the walk before the tree was exhausted.
    truncated: bool = False
    #: Why, in words a caller can act on.  Empty when nothing was cut.
    reason: str = ""
    #: Records the walk produced, before ``only_interactive`` filtering and ``max_nodes``.
    walked: int = 0

    def to_text(self, *, max_nodes: int, only_interactive: bool,
                fields: Sequence[str] = DEFAULT_FIELDS,
                handles: Optional[Mapping[int, str]] = None,
                unaddressable: Sequence[int] = (),
                root_note: str = "") -> "Rendered":
        """Render the bounded view.  Pure: no pyjab, no I/O, no state."""
        return render(
            self, max_nodes=max_nodes, only_interactive=only_interactive,
            fields=fields, handles=handles or {}, unaddressable=unaddressable,
            root_note=root_note,
        )


@dataclass(frozen=True)
class Rendered:
    """The text plus the measurements a caller needs to trust it."""

    text: str
    shown: int
    kept: int
    truncated: bool
    reason: str
    estimated_tokens: int
    #: The indices into ``Walk.nodes`` that were rendered, in order -- what the caller
    #: assigns handles to.
    rendered_indices: Tuple[int, ...] = ()
    #: Rendered nodes that could not be given a handle, so the text can say so rather than
    #: leaving the agent to wonder why one line has no ``[e:...]``.
    unhandleable: int = 0


def is_container(record: Record) -> bool:
    """Whether this node is pure layout scaffolding."""
    return record.role.strip().lower() in CONTAINER_ROLES


def _kept_flags(nodes: Sequence[Node], only_interactive: bool) -> List[bool]:
    """Which nodes survive ``only_interactive``.

    A node is kept when it is not a container, or is a named container, or has a kept
    descendant -- the last two because dropping them would remove structure the caller
    needs in order to read the view at all.
    """
    if not only_interactive:
        return [True] * len(nodes)

    kept = [not is_container(node.record) or bool(node.record.name) for node in nodes]
    # Bottom-up: document order guarantees children come after their parent, so walking
    # backwards means a parent sees its children's final verdict.
    for index in range(len(nodes) - 1, -1, -1):
        parent = nodes[index].parent
        if kept[index] and parent is not None:
            kept[parent] = True
    return kept


def estimate_tokens(text: str) -> int:
    """A conservative estimate of what *text* costs a model.

    Characters over four, which is the usual English ratio.  It is deliberately simple and
    deliberately stated: the real tokeniser belongs to the client, and a number this module
    invented is only good enough for the snapshot to report its own size and for the tests
    to hold a budget.  A label-heavy or CJK window can cost more than this says, which is
    why the default budgets have headroom rather than sitting at the limit.
    """
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


def render(
    walk: Walk,
    *,
    max_nodes: int,
    only_interactive: bool,
    fields: Sequence[str] = DEFAULT_FIELDS,
    handles: Mapping[int, str] = {},
    unaddressable: Sequence[int] = (),
    indent: str = "  ",
    root_note: str = "",
) -> Rendered:
    """One compact line per node, bounded, with an honest truncation notice.

    Args:
        walk: The bounded walk to render.
        max_nodes: Hard cap on rendered nodes. Reached means truncated, and the text says
            so, because a caller that cannot tell "cut short" from "this is all of it" will
            act on half a window.
        only_interactive: Drop pure containers (see the module docstring).
        fields: Extra record fields to print. Role and name are always printed: they are
            the identity a handle verifies against.
        handles: ``node index -> handle`` for the nodes that got one.
        unaddressable: Node indices the caller could not build a locator for.  Passed in
            rather than guessed: only the caller knows whether a missing handle means "not
            addressable" or "the table is full".
        indent: One level of indentation.
        root_note: Extra facts about the window itself (hwnd, pid) for the first line.

    Returns:
        A :class:`Rendered` whose ``text`` ends with either the truncation notice or
        nothing, never with a silent cut.
    """
    unknown = [name for name in fields if name not in KNOWN_FIELDS]
    if unknown:
        raise ValueError(
            f"unknown field(s) {unknown}; known fields are {list(KNOWN_FIELDS)}"
        )

    #: index -> how many children the walk produced. Counted over the *walk* rather than over
    #: the rendered lines: a child dropped by ``only_interactive`` is still a child the walk
    #: saw, and marking its parent would blame pyjab for this project's own filtering.
    walked_children: Dict[int, int] = {}
    for node in walk.nodes:
        if node.parent is not None:
            walked_children[node.parent] = walked_children.get(node.parent, 0) + 1

    kept = _kept_flags(walk.nodes, only_interactive)
    lines: List[str] = []
    rendered_indices: List[int] = []
    stopped_because_of_budget = False

    top_level = sum(1 for node in walk.nodes if node.parent is None)
    root_line = f"window {walk.root.label}"
    if "children" in fields:
        # pyjab's walk yields *descendants*, so the top-level nodes are the root's children.
        root_line += f" children={walk.root.children_count}"
        if top_level < walk.root.children_count:
            root_line += f" (+{walk.root.children_count - top_level})"
    if root_note:
        root_line += f" ({root_note})"
    lines.append(root_line)
    unaddressable_set = set(unaddressable)

    for index, node in enumerate(walk.nodes):
        if not kept[index]:
            continue
        if len(rendered_indices) >= max_nodes:
            stopped_because_of_budget = True
            break
        rendered_indices.append(index)
        lines.append(
            f"{indent * (node.depth + 1)}"
            f"{_line(node, fields, handles.get(index), walked_children.get(index, 0))}"
        )

    truncated = walk.truncated or stopped_because_of_budget
    reason = walk.reason if walk.truncated else ""
    if stopped_because_of_budget and not reason:
        reason = f"node budget reached ({max_nodes} shown)"
    elif stopped_because_of_budget and reason:
        reason = f"{reason}; node budget reached ({max_nodes} shown)"

    # Only when the field is printed at all: a legend for a marker nobody can see is noise.
    reported_more = "children" in fields and any(
        walked_children.get(index, 0) < walk.nodes[index].record.children_count
        for index in rendered_indices
    )
    if reported_more:
        lines.append(
            "A `(+n)` after children= means the walk produced n fewer children than the "
            "element reports. children= is the element's own total; a walk yields the "
            "children the accessibility tree exposes, so a collapsed node or an unselected "
            "tab reports children it does not show."
            + (" The walk was also cut short, so some of that gap may be its limit."
               if walk.truncated else "")
        )
    unhandleable = len(unaddressable_set.intersection(rendered_indices))
    if unhandleable:
        lines.append(
            f"{unhandleable} node(s) have no handle, so no later call can name them. "
            "Nothing is wrong with the window: their position among siblings is not "
            "reported, and a locator built without it would resolve to the wrong element."
        )
    text = "\n".join(lines)
    if truncated:
        text += (
            f"\ntruncated: {len(rendered_indices)} of {walk.walked} walked node(s) shown"
            f" -- {reason}. Narrow it with depth, max_nodes or only_interactive, or "
            "snapshot a handle to see inside it."
        )
    return Rendered(
        text=text,
        shown=1 + len(rendered_indices),
        kept=1 + sum(1 for flag in kept if flag),
        truncated=truncated,
        reason=reason,
        estimated_tokens=estimate_tokens(text),
        rendered_indices=tuple(rendered_indices),
        unhandleable=unhandleable,
    )


def _line(node: Node, fields: Sequence[str], handle: Optional[str],
          walked_children: Optional[int] = None) -> str:
    """``[handle] role "name" (states)`` and whichever extras were asked for.

    ``walked_children`` is how many children the walk actually produced for this node. When it
    is *fewer* than the record's ``children_count`` the line says so: pyjab reports the
    element's **total** child count while a walk yields the **visible** ones, so the two can
    disagree for a collapsed node or an unselected tab -- and a reader with no marker cannot
    tell that from a walk that stopped early, a rendering bug, or a read that failed.
    """
    record = node.record
    parts = [f"[{handle}]" if handle else "", record.label]
    parts = [part for part in parts if part]
    if "states" in fields and record.states:
        parts.append(f"({','.join(record.states)})")
    if "bounds" in fields and record.bounds:
        bounds = record.bounds
        parts.append(
            f"bounds=({bounds.get('x')},{bounds.get('y')},"
            f"{bounds.get('width')}x{bounds.get('height')})"
        )
    if "description" in fields and record.description:
        parts.append(f"desc={record.description!r}")
    if "children" in fields:
        marker = ""
        if walked_children is not None and walked_children < record.children_count:
            marker = f" (+{record.children_count - walked_children})"
        parts.append(f"children={record.children_count}{marker}")
    if "index" in fields:
        parts.append(f"index={record.index_in_parent}")
    if "depth" in fields:
        parts.append(f"depth={record.object_depth}")
    return " ".join(parts)


def walked_nodes(records: Iterable[Tuple[int, Mapping[str, Any]]]) -> Tuple[Node, ...]:
    """Turn pyjab's ``(depth, record)`` stream into nodes with parent links.

    ``role_position`` is computed here rather than read from the record, because pyjab's
    ``[n]`` XPath position counts *siblings with the same role*, while ``index_in_parent``
    counts all siblings.  A locator written with the wrong one silently points at a
    different element.
    """
    nodes: List[Node] = []
    #: depth -> index of the most recent node at that depth, and its role counters
    stack: Dict[int, int] = {}
    counters: Dict[int, Dict[str, int]] = {}

    for depth, raw in records:
        record = Record.from_pyjab(raw)
        parent = stack.get(depth - 1) if depth > 0 else None
        seen = counters.setdefault(depth, {})
        seen[record.role] = seen.get(record.role, 0) + 1
        nodes.append(Node(record=record, depth=depth, parent=parent,
                          role_position=seen[record.role]))
        stack[depth] = len(nodes) - 1
        # A shallower node later in document order invalidates the deeper stack entries;
        # leaving them would attach a sibling's child to the wrong parent.
        for deeper in [d for d in stack if d > depth]:
            del stack[deeper]
            counters.pop(deeper, None)
    return tuple(nodes)
