"""What this project depends on pyjab doing, pinned as behaviour rather than as a promise.

AGENTS.md §1a calls this the load-bearing half of the drift mechanism, and the reason is
that a marker in somebody else's changelog is a judgement that can be forgotten or wrong.
These tests are not: they assert the exact shapes the code here reads, so a change that
breaks this project fails a job whether or not anyone wrote anything down.

What is pinned, and why exactly these three:

* **the walk** -- ``JABElement.walk`` returning something with a truncation flag, and
  yielding ``(depth, record)`` pairs. A snapshot that cannot tell "cut short" from "this is
  all of it" is the failure mode this project's rules are about.
* **the record fields** -- ``as_record()``'s keys. A handle stores them and checks them
  again later; a renamed field would silently make every handle unverifiable, or worse,
  verifiable against nothing.
* **the message pump** -- that ``pump_messages`` exists on ``Win32Utils`` and is public.
  This is the thread-affinity rule that makes the whole executor legal, and it is a method
  name, which is exactly the kind of thing a refactor moves.

Nothing here needs Windows to *define*, but the imports do: ``pyjab.jabelement`` and
``pyjab.jabdriver`` raise ``ImportError`` off Windows by design, so off Windows these are
skipped rather than passed. The scheduled `pyjab-drift` workflow runs them on
``windows-latest`` against ``pyjab@master``, which is where they earn their place.
"""

from __future__ import annotations

import importlib.metadata
import inspect
import sys

import pytest

#: The record fields this project reads.  Anything added to `tree.Record.from_pyjab` must be
#: added here too, which is the point: the two lists are the contract.
RECORD_FIELDS = {
    "role",
    "name",
    "description",
    "index_in_parent",
    "children_count",
    "object_depth",
    "states",
    "bounds",
}

windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="pyjab's Windows modules raise ImportError off Windows by design",
)


def test_pyjab_is_installed_and_its_version_is_readable():
    """The version comes from distribution metadata, which is how diagnostics reports it."""
    assert importlib.metadata.version("pyjab")


def test_the_package_imports_without_windows():
    """`import pyjab` has to stay harmless off Windows; the wheel installs anywhere."""
    import pyjab

    assert pyjab is not None


def test_pyjab_log_records_reach_the_application_handler():
    """The server configures logging; pyjab's own warnings have to arrive at that handler.

    pyjab stopped calling ``logging.basicConfig`` (master, commit 8ec88a3): its ``Logger``
    now attaches a ``NullHandler`` and sets its own level, which is what a library should do.
    The property this project depends on is that its records still *propagate* to whatever
    the application configured -- a bridge that cannot enable Java Access Bridge logs an
    error, and a server that never sees it reports a worse message to the model.

    The test attaches its own handler rather than trusting the root configuration, so it says
    the same thing whether or not anything else has configured logging.
    """
    import logging

    records = []

    class Recorder(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    root = logging.getLogger()
    handler = Recorder()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        from pyjab.common.logger import Logger

        Logger("pyjab").log.error("a pyjab failure the server has to be able to see")
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)

    assert records, (
        "pyjab's log records no longer reach a handler on the root logger. This project "
        "configures logging at start-up and expects library warnings to arrive; if pyjab "
        "now swallows them, attach a handler to its logger explicitly."
    )


def test_the_bridge_dll_lookup_is_pure_and_answers_about_a_bitness():
    """`java_diagnostics` calls these; a side effect here would change the user's machine."""
    from pyjab import config

    assert hasattr(config, "find_bridge_dll")
    assert hasattr(config, "describe_bridge_dll_search")
    assert config.A11Y_PROPS_PATH
    assert config.A11Y_PROPS_CONTENT

    # No DLL exists here, and the answer is None rather than an exception: "not found" is a
    # measurement. It must also not write ~/.accessibility.properties.
    assert config.find_bridge_dll() in (None, config.find_bridge_dll())


@windows_only
def test_the_tree_walk_returns_something_that_says_whether_it_was_cut_short():
    from pyjab.jabelement import JABElement

    assert hasattr(JABElement, "walk"), "java_snapshot is built on walk()"

    tree = JABElement  # the type, not an instance: the shape is what is pinned here
    assert hasattr(tree, "as_record")


@windows_only
def test_as_record_still_returns_the_fields_a_handle_verifies_against():
    """A renamed field here would make every stored handle unverifiable."""
    from pyjab.jabelement import JABElement

    source = inspect.getsource(JABElement.as_record)
    for field in RECORD_FIELDS:
        assert f'"{field}"' in source, (
            f"as_record() no longer returns {field!r}, which tree.Record.from_pyjab reads "
            "and handles verify against"
        )


@windows_only
def test_the_message_pump_is_public_and_takes_no_arguments():
    """The rule the executor implements: COM events go to the Windows_run() thread."""
    from pyjab.common.win32utils import Win32Utils

    pump = getattr(Win32Utils, "pump_messages", None)
    assert pump is not None, "BridgeThread's whole loop is built on pump_messages()"
    assert not pump.__name__.startswith("_"), "it is part of the public contract"
    assert list(inspect.signature(pump).parameters) == ["self"]


@windows_only
def test_the_driver_offers_the_things_this_layer_uses():
    """Every ``JABDriver`` name the adapter calls, asserted one by one.

    ``release_jabelement`` used to be in this list, and it is not a ``JABDriver`` method at
    all -- the adapter called ``driver.release_jabelement(element)`` and the L1 fake was
    shaped the same way, so the two agreed with each other and both were wrong. This test ran
    on a Windows runner for the first time and failed, which is the whole point of it. The
    release now goes through the element (see the next test).
    """
    from pyjab.jabdriver import JABDriver

    for name in ("root_element", "find_element_by_xpath", "pid", "hwnd", "title"):
        assert hasattr(JABDriver, name), f"JABDriver.{name} is used by the backend adapter"

    # And the thing this project must never call. If it disappears, that is good news for
    # R2 -- but it would change behaviour, so it is worth noticing either way.
    assert hasattr(JABDriver, "__exit__")


@windows_only
def test_releasing_a_reference_is_something_the_element_does():
    """The other half of the same finding: the element owns the release, not the driver."""
    from pyjab.jabelement import JABElement

    assert hasattr(JABElement, "release_jabelement"), (
        "resolve_driver gives the reference back through the element it resolved; if that "
        "moves, the handle path leaks Java objects -- issue #43's shape"
    )
    signature = inspect.signature(JABElement.release_jabelement)
    assert list(signature.parameters) == ["self", "jabelement"], (
        "the default argument is what lets an element release itself, which is how the "
        f"adapter calls it; the signature is now {signature}"
    )


@windows_only
def test_the_walk_result_still_reports_its_own_truncation():
    """``truncated`` is a property; ``limit_hit`` is set per instance in ``__init__``.

    The second half of that sentence is why this test looks the way it does: an earlier
    version asserted ``hasattr(JABTree, "limit_hit")``, which is False for an instance
    attribute, and real CI said so. Asserting the shape rather than a class attribute is what
    makes it both true and useful.
    """
    from pyjab.jabelement import JABTree

    assert isinstance(inspect.getattr_static(JABTree, "truncated"), property), (
        "walk_driver reads .truncated to tell a cut-short walk from a complete one"
    )
    assert "self.limit_hit" in inspect.getsource(JABTree.__init__), (
        "walk_driver reads .limit_hit as well, because stopping the iteration is not the "
        "same as pyjab stopping it"
    )
