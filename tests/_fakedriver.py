"""A fake driver, and a fake tree, that refuse the two mistakes this layer can make.

Modelled on pyjab's ``tests/_fakejab.py``, which is a reference-counting bridge that raises
on a double release and on use after release -- and which found two real leaks. The point is
the same here, and it is the only way the ownership rule in ``backend.walk_driver`` and
``backend.resolve_driver`` can be tested at all off Windows: those two functions take the
driver as an argument rather than reaching for one, so this file can stand in for pyjab.

What it refuses:

* **Use after release** (:class:`UseAfterRelease`) -- an element whose reference was given
  back and which is then read.
* **A double release** (:class:`DoubleRelease`) -- the same element released twice, which is
  how a reference count gets corrupted rather than crashing.
* **An abandoned walk** -- :class:`FakeTree` hands out an iterator object with a ``close()``
  that the walker has to call when it stops early, and records whether it was called. A real
  generator cannot test this: dropping the last reference closes it too, so a missing
  ``close()`` would look identical to a correct one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


class UseAfterRelease(AssertionError):
    """An element was read after its reference was given back."""


class DoubleRelease(AssertionError):
    """An element was released twice."""


def record(
    role: str = "push button",
    name: str = "OK",
    *,
    index_in_parent: int = 0,
    children_count: int = 0,
    object_depth: int = 1,
    states: str = "enabled,visible",
    bounds: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    """One pyjab ``as_record()``-shaped dict, with the fields that matter defaulted."""
    return {
        "role": role,
        "name": name,
        "description": "",
        "index_in_parent": index_in_parent,
        "children_count": children_count,
        "object_depth": object_depth,
        "states": states,
        "bounds": dict(bounds) if bounds else None,
    }


class FakeElement:
    """An element that owns one reference, and knows when it has been given back."""

    def __init__(self, data: Mapping[str, Any], driver: "FakeDriver", path: str) -> None:
        self._data = data
        self._driver = driver
        self.path = path
        self.released = False

    def as_record(self) -> Dict[str, Any]:
        if self.released:
            raise UseAfterRelease(f"{self.path} was read after release")
        if self.path in self._driver.explode:
            # A read that raises is the interesting case for ownership: the reference has
            # been taken and the caller still has to give it back.
            raise RuntimeError(f"the bridge refused to read {self.path}")
        return dict(self._data)

    def walk(self, max_depth: Optional[int] = None,
             limit: Optional[int] = None) -> "FakeTree":
        """pyjab's ``JABElement.walk``, in the shape ``walk_driver`` expects."""
        return self._driver.make_tree(max_depth, limit)


class _FakeIterator:
    """What ``FakeTree.__iter__`` returns: an iterator with a ``close()`` that matters.

    A plain generator would hide a missing ``close()``, because dropping its last reference
    closes it as well. This object records the difference, so a walker that stops early
    without closing it fails a test rather than passing one.
    """

    def __init__(self, items: Sequence[Tuple[int, Mapping[str, Any]]], tree: "FakeTree") -> None:
        self._items = list(items)
        self._tree = tree
        self._cursor = 0

    def __iter__(self) -> "_FakeIterator":
        return self

    def __next__(self) -> Tuple[int, Mapping[str, Any]]:
        if self._tree.closed:
            raise AssertionError("iterated after close()")
        if self._cursor >= len(self._items):
            self._tree.exhausted = True
            raise StopIteration
        item = self._items[self._cursor]
        self._cursor += 1
        return item

    def close(self) -> None:
        self._tree.closed = True


class FakeTree:
    """The object ``FakeElement.walk()`` returns: an iterable plus pyjab's truncation flag."""

    def __init__(
        self,
        items: Sequence[Tuple[int, Mapping[str, Any]]],
        *,
        limit: Optional[int] = None,
    ) -> None:
        self._items = list(items)
        self.closed = False
        self.exhausted = False
        self.limit_hit = False
        self._limit = limit

    def __iter__(self) -> _FakeIterator:
        items = self._items
        if self._limit is not None and len(items) > self._limit:
            # Simulate pyjab's own cap: it stops and says so, and the records it does produce
            # look exactly like a complete tree's.
            items = items[: self._limit]
            self.limit_hit = True
        return _FakeIterator(items, self)


class FakeDriver:
    """A stand-in for ``JABDriver`` with a reference count.

    Args:
        root: The window's own record.
        items: ``(depth, record)`` pairs the walk yields.
        locators: ``locator -> record`` for elements that re-resolve, and
            ``locator -> Exception`` for elements that do not.
        walk_limit: When set, the walk stops there and reports ``limit_hit``.
        explode: Locators whose *read* raises after the element was handed over.
    """

    def __init__(
        self,
        *,
        root: Optional[Mapping[str, Any]] = None,
        items: Sequence[Tuple[int, Mapping[str, Any]]] = (),
        locators: Optional[Mapping[str, Any]] = None,
        walk_limit: Optional[int] = None,
        explode: Sequence[str] = (),
    ) -> None:
        self._root = FakeElement(
            root or record("root pane", "", children_count=len(items)), self, "root"
        )
        self._items = list(items)
        self._locators = dict(locators or {})
        self._walk_limit = walk_limit
        self.explode = set(explode)
        self.outstanding = 0
        self.handed_out = 0
        self.walk_calls: List[Dict[str, Any]] = []
        self.trees: List[FakeTree] = []

    # -- what walk_driver uses ------------------------------------------------------

    @property
    def root_element(self) -> FakeElement:
        return self._root

    # -- what resolve_driver uses ---------------------------------------------------

    def find_element_by_xpath(self, locator: str) -> FakeElement:
        if locator not in self._locators:
            raise LookupError(f"no element matches {locator!r}")
        target = self._locators[locator]
        if isinstance(target, Exception):
            raise target
        self.outstanding += 1
        self.handed_out += 1
        return FakeElement(target, self, locator)

    def release_jabelement(self, element: FakeElement) -> None:
        if element.released:
            raise DoubleRelease(f"{element.path} was released twice")
        element.released = True
        self.outstanding -= 1

    # -- convenience -----------------------------------------------------------------

    def make_tree(self, max_depth: Optional[int], limit: Optional[int]) -> FakeTree:
        items = list(self._items)
        if max_depth is not None:
            items = [(depth, data) for depth, data in items if depth <= max_depth]
        self.walk_calls.append({"max_depth": max_depth, "limit": limit})
        tree = FakeTree(items, limit=self._walk_limit)
        self.trees.append(tree)
        return tree
