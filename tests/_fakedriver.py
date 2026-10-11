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

**A release is something the element does** (``element.release_jabelement()``), because that
is where pyjab puts it -- ``JABDriver`` has no such method, and an earlier version of this
file mirrored the adapter's guess instead of pyjab, so both agreed and both were wrong. The
accounting still lives on the driver, which is the bridge in this picture.
* **An abandoned walk** -- :class:`FakeTree` hands out an iterator object with a ``close()``
  that the walker has to call when it stops early, and records whether it was called. A real
  generator cannot test this: dropping the last reference closes it too, so a missing
  ``close()`` would look identical to a correct one.
"""

from __future__ import annotations

import sys
import types
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

    def _read(self) -> None:
        """Both reads go through here, so 'a read that raises' covers text as well."""
        if self.released:
            raise UseAfterRelease(f"{self.path} was read after release")
        if self.path in self._driver.explode:
            # The interesting case for ownership: the reference has been taken and the caller
            # still has to give it back.
            raise RuntimeError(f"the bridge refused to read {self.path}")

    def as_record(self) -> Dict[str, Any]:
        self._read()
        return dict(self._data)

    def walk(self, max_depth: Optional[int] = None,
             limit: Optional[int] = None) -> "FakeTree":
        """pyjab's ``JABElement.walk``, in the shape ``walk_driver`` expects."""
        return self._driver.make_tree(max_depth, limit)

    @property
    def table(self) -> "Optional[Dict[str, int]]":
        """pyjab's ``JABElement.table``: the counts, or None when this is not a table."""
        self._read()
        return self._driver.table_info(self.path)

    def get_cell(self, row: int, column: int, visible: bool = False) -> "FakeElement":
        """pyjab's ``JABElement.get_cell``: one cell, owned by the caller."""
        self._read()
        return self._driver.cell(self.path, row, column)

    @property
    def text(self) -> "Optional[str]":
        """pyjab's ``JABElement.text``: the whole string, None when there is no text interface."""
        self._read()
        cell_text = getattr(self, "_cell_text", "unset")
        if cell_text != "unset":
            return cell_text
        return self._driver.element_text(self.path)

    # -- the actions (M5), recorded on the driver so a test can see what ran --------------

    def _acted(self, action: str, **kwargs) -> None:
        self._read()
        self._driver.actions.append({"path": self.path, "action": action, **kwargs})

    def click(self, simulate: bool = False) -> None:
        self._acted("click", simulate=simulate)

    def double_click(self) -> None:
        self._acted("double_click")

    def context_click(self) -> None:
        self._acted("context_click")

    def send_text(self, value: Any, simulate: bool = False,
                  wait_for_text_update: bool = True) -> None:
        self._acted("send_text", value=value, simulate=simulate)

    def clear(self, simulate: bool = False, wait_for_text_update: bool = True) -> None:
        self._acted("clear", simulate=simulate)

    def select(self, option: Any, simulate: bool = False,
               wait_for_selection: bool = True) -> None:
        self._acted("select", option=option, simulate=simulate)

    def select_cell(self, row: int, column: int, clear: bool = True) -> None:
        self._acted("select_cell", row=row, column=column)

    def select_row(self, row: int, clear: bool = True) -> None:
        self._acted("select_row", row=row)

    def release_jabelement(self) -> None:
        """pyjab's ``JABElement.release_jabelement``: the element gives its own reference back.

        ``JABDriver`` has no equivalent, which is the detail that cost a real Windows run --
        see the module docstring.
        """
        if self.released:
            raise DoubleRelease(f"{self.path} was released twice")
        self.released = True
        self._driver.outstanding -= 1


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
    #: The names pyjab's virtual-key table has, near enough for a fake that raises on the
    #: same input the real one raises on.
    VIRTUAL_KEYS = frozenset({
        "ctrl", "alt", "shift", "tab", "enter", "escape", "spacebar", "left_arrow",
        "right_arrow", "up_arrow", "down_arrow",
    }) | frozenset("abcdefghijklmnopqrstuvwxyz0123456789")

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
        search: Optional[Mapping[Any, Sequence[Mapping[str, Any]]]] = None,
        text: Optional[Mapping[str, Optional[str]]] = None,
        tables: Optional[Mapping[str, Sequence[Sequence[Any]]]] = None,
        cells_that_raise: Sequence[Any] = (),
        cells_that_are_empty: Sequence[Any] = (),
    ) -> None:
        self._root = FakeElement(
            root or record("root pane", "", children_count=len(items)), self, "root"
        )
        self._items = list(items)
        self._locators = dict(locators or {})
        self._walk_limit = walk_limit
        self.explode = set(explode)
        self._search_records = {key: list(value) for key, value in (search or {}).items()}
        self._text = dict(text or {})
        self._tables = {key: [list(row) for row in value] for key, value in (tables or {}).items()}
        self.cells_that_raise = set(cells_that_raise)
        self.cells_that_are_empty = set(cells_that_are_empty)
        self.cells_handed_out = 0
        self.searches: List[Dict[str, Any]] = []
        #: Every action a test caused, in order, with what it was asked for.
        self.actions: List[Dict[str, Any]] = []
        self.detached = False
        #: Every chord a test caused, in order.
        self.keys_sent: List[str] = []
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

    # -- what find_driver uses -------------------------------------------------------

    def search_elements(self, by: str, value: Any) -> "List[FakeElement]":
        self.searches.append({"by": by, "value": value})
        records = self._search_records.get((by, value), [])
        elements = []
        for index, data in enumerate(records):
            self.outstanding += 1
            self.handed_out += 1
            elements.append(FakeElement(data, self, f"{by}:{value}:{index}"))
        return elements

    def find_elements_by_name(self, value: Any, visible: bool = False) -> "List[FakeElement]":
        return self.search_elements("name", value)

    def find_elements_by_role(self, value: Any, visible: bool = False) -> "List[FakeElement]":
        return self.search_elements("role", value)

    def find_elements_by_description(self, value: Any, visible: bool = False) -> "List[FakeElement]":
        return self.search_elements("description", value)

    def find_elements_by_states(self, value: Any, visible: bool = False) -> "List[FakeElement]":
        return self.search_elements("states", value)

    def find_elements_by_xpath(self, value: Any, visible: bool = False) -> "List[FakeElement]":
        return self.search_elements("xpath", value)

    def element_text(self, path: str) -> "Optional[str]":
        return self._text.get(path)

    def table_info(self, path: str) -> "Optional[Dict[str, int]]":
        grid = self._tables.get(path)
        if grid is None:
            return None
        return {
            "row_count": len(grid),
            "column_count": len(grid[0]) if grid else 0,
        }

    def cell(self, path: str, row: int, column: int) -> FakeElement:
        grid = self._tables.get(path)
        if grid is None or row >= len(grid) or column >= len(grid[row]):
            raise IndexError(f"no cell at ({row}, {column}) in {path}")
        marker = (path, row, column)
        if marker in self.cells_that_raise:
            raise RuntimeError(f"the bridge refused to read the cell at ({row}, {column})")
        value = None if marker in self.cells_that_are_empty else grid[row][column]
        self.outstanding += 1
        self.handed_out += 1
        self.cells_handed_out += 1
        element = FakeElement(record("label", f"cell {row},{column}"), self,
                              f"{path}#cell({row},{column})")
        element._cell_text = value
        return element

    # -- what PyjabBackend.detach uses ------------------------------------------------

    def send_keys(self, keys: str) -> None:
        """pyjab's ``JABDriver.send_keys``: the chord goes to whatever has focus.

        No element is involved and nothing is clicked, which is why this is on the driver and
        not on the element: pyjab puts the primitive on all three, and this server calls the
        driver's because ``java_send_keys`` takes no element.
        """
        if not keys or not keys.strip():
            raise ValueError("send_keys() needs a key or a chord, such as 'alt+y'")
        unknown = [name for name in keys.split("+") if name.lower() not in self.VIRTUAL_KEYS]
        if unknown:
            raise ValueError(f"unknown key name(s) {unknown} in {keys!r}")
        self.keys_sent.append(keys)

    def detach(self) -> None:
        """pyjab's ``JABDriver.detach``: release the binding, leave the process alone.

        On the *driver*, not the element: pyjab puts it there, and a fake that mirrors the
        adapter's guess instead of pyjab is how an earlier version of this file agreed with a
        call to a method that does not exist.
        """
        self.detached = True

    # -- convenience -----------------------------------------------------------------

    def make_tree(self, max_depth: Optional[int], limit: Optional[int]) -> FakeTree:
        items = list(self._items)
        if max_depth is not None:
            items = [(depth, data) for depth, data in items if depth <= max_depth]
        self.walk_calls.append({"max_depth": max_depth, "limit": limit})
        tree = FakeTree(items, limit=self._walk_limit)
        self.trees.append(tree)
        return tree


def install_fake_jabdriver(monkeypatch: Any) -> "List[FakeDriver]":
    """Stand in for the ``pyjab.jabdriver`` module, and return the drivers built through it.

    ``PyjabBackend.attach`` does ``from pyjab.jabdriver import JABDriver`` inside the call, and
    that module raises pyjab's descriptive ``ImportError`` off Windows -- so a test that wants
    the *real* ``attach`` rather than a whole fake backend has to put a module in its place.
    The class planted there is a :class:`FakeDriver` carrying the three attributes ``attach``
    reads off a freshly bound driver -- ``hwnd``, ``title`` and ``pid`` -- and every instance
    is appended to the returned list, because "the earlier binding was released" can only be
    asserted against the instances: ``PyjabBackend`` never hands one out.

    ``monkeypatch`` restores ``sys.modules`` afterwards, so the stub cannot leak into another
    test -- which also means a bridge thread must not still be using it when the test ends.
    """
    instances: "List[FakeDriver]" = []

    class JABDriver(FakeDriver):
        """Whatever ``attach`` passes, plus the three attributes it then reads off it."""

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(items=[(0, record("push button", "OK"))])
            self.hwnd = int(kwargs.get("hwnd") or 0x10001)
            self.title = str(kwargs.get("title") or "Test Application")
            self.pid = 4242
            instances.append(self)

    module = types.ModuleType("pyjab.jabdriver")
    module.JABDriver = JABDriver
    monkeypatch.setitem(sys.modules, "pyjab.jabdriver", module)
    return instances
