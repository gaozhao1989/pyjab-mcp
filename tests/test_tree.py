"""L1 tests for the bounded view: what a snapshot shows, and what it admits it left out.

The rule under test is this project's first constraint (``docs/ARCHITECTURE.md``): never
return the whole tree, and never let a bounded view look like a complete one. A snapshot that
silently omits half a window is indistinguishable from a window that has half as much in it,
and the caller will act on it -- a measurement that could not be taken is not a zero, applied to a tool result.

Everything here is pure: a fake tree in, text and measurements out.
"""

from __future__ import annotations

import pytest

from pyjab_mcp.backend import PyjabUnavailable
from pyjab_mcp.handles import locator_for, verify
from pyjab_mcp.tree import (
    DEFAULT_FIELDS,
    KNOWN_FIELDS,
    Record,
    Walk,
    estimate_tokens,
    is_container,
    render,
    walked_nodes,
)
from tests._fakedriver import record

ROOT = record("root pane", "App")


def walk_of(items, root=ROOT) -> Walk:
    nodes = walked_nodes(items)
    return Walk(root=Record.from_pyjab(root), nodes=nodes, walked=len(items))


def test_a_small_tree_renders_one_line_per_node_with_indentation():
    walk = walk_of([
        (0, record("panel", "Form")),
        (1, record("push button", "OK")),
    ])

    rendered = walk.to_text(max_nodes=10, only_interactive=False, handles={})

    assert rendered.text.splitlines() == [
        'window root pane "App"',
        '  panel "Form" (enabled,visible)',
        '    push button "OK" (enabled,visible)',
    ]
    assert rendered.truncated is False
    assert rendered.shown == 3


def test_pure_containers_are_dropped_but_structure_is_kept():
    """A panel is dropped; the panel *above* a button is kept, or the view loses the tree."""
    walk = walk_of([
        (0, record("panel", "")),                      # scaffolding, no kept children
        (0, record("scroll pane", "")),
        (1, record("panel", "")),
        (2, record("push button", "OK")),
    ])

    rendered = walk.to_text(max_nodes=10, only_interactive=True, handles={})
    lines = rendered.text.splitlines()

    assert not any('"Form"' in line for line in lines)
    assert any("scroll pane" in line for line in lines), (
        "the ancestor of a kept node has to stay, or the indentation means nothing"
    )
    assert any("push button" in line for line in lines)


def test_a_named_container_is_kept_because_a_name_is_information():
    walk = walk_of([(0, record("panel", "Update"))])

    rendered = walk.to_text(max_nodes=10, only_interactive=True, handles={})

    assert 'panel "Update"' in rendered.text


def test_the_node_budget_cuts_the_view_and_says_so():
    walk = walk_of([
        (0, record("push button", f"B{index}", index_in_parent=index))
        for index in range(10)
    ])

    rendered = walk.to_text(max_nodes=3, only_interactive=True, handles={})

    assert rendered.truncated is True
    assert "node budget reached (3 shown)" in rendered.text
    assert "truncated:" in rendered.text
    assert len(rendered.rendered_indices) == 3


def test_a_walk_that_pyjab_cut_is_reported_with_pyjabs_reason():
    nodes = walked_nodes([(0, record("push button", "B0"))])
    walk = Walk(root=Record.from_pyjab(ROOT), nodes=nodes, walked=1,
                truncated=True, reason="raw walk cap reached (2 records)")

    rendered = walk.to_text(max_nodes=10, only_interactive=False, handles={})

    assert rendered.truncated is True
    assert "raw walk cap reached" in rendered.text


def test_a_complete_view_says_nothing_about_truncation():
    walk = walk_of([(0, record("push button", "OK"))])

    rendered = walk.to_text(max_nodes=10, only_interactive=False, handles={})

    assert "truncated" not in rendered.text


def test_handles_appear_on_the_lines_that_have_them():
    walk = walk_of([(0, record("push button", "OK"))])

    rendered = walk.to_text(max_nodes=10, only_interactive=False, handles={0: "e:1234"})

    assert "[e:1234] push button" in rendered.text


def test_nodes_that_cannot_be_addressed_are_counted_in_the_text():
    """The agent should not have to work out why one line has no handle."""
    walk = walk_of([(0, record("push button", "OK"))])

    rendered = walk.to_text(
        max_nodes=10, only_interactive=False, handles={}, unaddressable=[0]
    )

    assert rendered.unhandleable == 1
    assert "1 node(s) have no handle" in rendered.text


def test_the_default_budget_stays_under_the_token_target():
    """The release criterion is a snapshot under 4k tokens, and it is asserted, not hoped."""
    items = []
    for index in range(400):
        items.append((0, record("panel", f"Panel {index}", index_in_parent=index)))
        items.append((1, record(
            "push button", f"Button {index} with a fairly long accessible name",
            index_in_parent=0,
        )))
    walk = walk_of(items)

    rendered = walk.to_text(
        max_nodes=120, only_interactive=True, fields=DEFAULT_FIELDS, handles={}
    )

    assert rendered.truncated is True
    assert rendered.estimated_tokens < 4000, (
        f"the default snapshot costs about {rendered.estimated_tokens} tokens; "
        "staying under 4k tokens by default is a release criterion"
    )


def test_the_renderer_refuses_a_field_it_does_not_have():
    """A typo in `fields` must not be silently ignored -- the agent asked for something."""
    walk = walk_of([(0, record("push button", "OK"))])

    with pytest.raises(ValueError, match="unknown field"):
        walk.to_text(max_nodes=5, only_interactive=False, fields=("colour",))

    assert "bounds" in KNOWN_FIELDS


def test_extra_fields_are_printed_when_asked_for():
    walk = walk_of([(0, record(
        "push button", "OK", children_count=3, object_depth=4,
        bounds={"x": 1, "y": 2, "width": 3, "height": 4},
    ))])

    rendered = walk.to_text(
        max_nodes=5, only_interactive=False,
        fields=("bounds", "children", "index", "depth"), handles={},
    )

    line = rendered.text.splitlines()[1]
    assert "bounds=(1,2,3x4)" in line
    assert "children=3" in line
    assert "index=0" in line
    assert "depth=4" in line


def test_estimating_tokens_is_monotonic_and_never_zero():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("a" * 400) < estimate_tokens("a" * 800)


def test_container_roles_are_compared_without_case_or_padding():
    assert is_container(Record.from_pyjab(record(" Panel ", ""))) is True
    assert is_container(Record.from_pyjab(record("push button", ""))) is False


# -- locators and verification ------------------------------------------------------


def test_a_locator_is_built_from_index_in_parent_not_from_role_position():
    """`indexinparent` comes from the node; a role position would depend on the walk being
    complete, so a truncated walk would produce a quietly wrong locator."""
    walk = walk_of([
        (0, record("panel", "A", index_in_parent=0)),
        (1, record("push button", "One", index_in_parent=0)),
        (1, record("push button", "Two", index_in_parent=1)),
    ])

    assert locator_for(walk, 1) == "//panel[@indexinparent=0]/push button[@indexinparent=0]"
    assert locator_for(walk, 2) == "//panel[@indexinparent=0]/push button[@indexinparent=1]"


def test_a_node_with_an_unknown_position_gets_no_locator():
    walk = walk_of([(0, record("push button", "?", index_in_parent=-1))])

    assert locator_for(walk, 0) is None


def test_a_node_with_no_role_gets_no_locator():
    walk = walk_of([(0, record("", "nameless"))])

    assert locator_for(walk, 0) is None


def test_an_index_outside_the_walk_gets_no_locator():
    walk = walk_of([(0, record("push button", "OK"))])

    assert locator_for(walk, 5) is None
    assert locator_for(walk, -1) is None


def test_verification_accepts_the_same_element():
    expected = Record.from_pyjab(record("push button", "OK", index_in_parent=2))

    assert verify(expected, expected).ok is True


@pytest.mark.parametrize("field,value", [
    ("role", "panel"),
    ("name", "Cancel"),
    ("index_in_parent", 3),
])
def test_verification_fails_on_any_identity_change(field, value):
    expected = Record.from_pyjab(record("push button", "OK", index_in_parent=2))
    changed = Record.from_pyjab({**record("push button", "OK", index_in_parent=2), field: value})

    result = verify(expected, changed)

    assert result.ok is False
    assert field.split("_")[0] in result.message() or field in result.message()


def test_verification_fails_when_the_element_moved():
    expected = Record.from_pyjab(record("push button", "OK", bounds={"x": 1, "y": 2, "width": 3, "height": 4}))
    moved = Record.from_pyjab(record("push button", "OK", bounds={"x": 9, "y": 2, "width": 3, "height": 4}))

    result = verify(expected, moved)

    assert result.ok is False
    assert "bounds" in result.message()


def test_verification_ignores_bounds_when_either_side_has_none():
    """A bridge that reports no geometry is not evidence that the element moved."""
    with_bounds = Record.from_pyjab(record("push button", "OK", bounds={"x": 1}))
    without = Record.from_pyjab(record("push button", "OK"))

    assert verify(with_bounds, without).ok is True
    assert verify(without, with_bounds).ok is True


def test_a_name_change_on_a_content_role_is_reported_but_does_not_block():
    """A text field's accessible name *is* its text, so an edit renames it.

    Found by the first real run: L3 set a field to "hello" and could no longer read it back,
    because the edit had renamed the element and a strict name check refused. The structural
    identity -- role, position, bounds -- is what catches a different element arriving, and it
    is what blocks.
    """
    before = Record.from_pyjab(record("text", "1122233455", index_in_parent=3))
    after = Record.from_pyjab(record("text", "hello from l3", index_in_parent=3))

    result = verify(before, after)

    assert result.ok is True, "an edit is not a different element"
    assert result.problems == ()
    assert result.warnings and "reports its content as its name" in result.warnings[0]
    assert "same element with different content" in result.message()


@pytest.mark.parametrize("role", ["text", "password text", "label", "table cell"])
def test_every_content_role_is_treated_as_content(role):
    before = Record.from_pyjab(record(role, "before", index_in_parent=1))
    after = Record.from_pyjab(record(role, "after", index_in_parent=1))

    assert verify(before, after).ok is True


@pytest.mark.parametrize("role", ["push button", "menu item", "check box", "tab"])
def test_a_label_role_that_renames_is_a_different_element(role):
    """The case D2 exists for: a renamed button at the same position is a different button."""
    before = Record.from_pyjab(record(role, "Save", index_in_parent=1))
    after = Record.from_pyjab(record(role, "Cancel", index_in_parent=1))

    result = verify(before, after)

    assert result.ok is False
    assert "name was 'Save', now 'Cancel'" in result.problems[0]


def test_a_content_role_that_also_moved_still_blocks():
    """The name is content; where the element *is* is not."""
    before = Record.from_pyjab(record("text", "before", index_in_parent=1))
    after = Record.from_pyjab(record("text", "after", index_in_parent=2))

    result = verify(before, after)

    assert result.ok is False
    assert "index among siblings" in result.problems[0]

# ---------------------------------------------------------------------------------
# Two answers to "how many children": pyjab's total and the walk's visible ones
# ---------------------------------------------------------------------------------

#: A window whose root reports two children while the walk produced one -- the case pyjab
#: describes in its own issue: `children_count` is the total, a walk yields the visible.
HIDDEN_CHILD_WINDOW = (
    (0, {"role": "panel", "name": "Container with a hidden child", "children_count": 2,
         "index_in_parent": 0, "object_depth": 1, "states": "", "description": ""}),
    (1, {"role": "push button", "name": "Only the visible one", "children_count": 0,
         "index_in_parent": 0, "object_depth": 2, "states": "", "description": ""}),
)


def window_walk(root_children: int = 2) -> Walk:
    records = list(HIDDEN_CHILD_WINDOW)
    root = Record.from_pyjab({
        "role": "frame", "name": "Window", "children_count": root_children,
        "index_in_parent": 0, "object_depth": 0, "states": "", "description": "",
    })
    return Walk(root=root, nodes=walked_nodes(records), walked=len(records))


def test_a_node_that_reports_more_children_than_the_walk_found_says_so():
    """Left unsaid, a reader cannot tell an invisible child from a truncated walk or a bug."""
    rendered = render(window_walk(), max_nodes=50, only_interactive=False,
                      fields=("children",))

    assert 'panel "Container with a hidden child" children=2 (+1)' in rendered.text
    assert "A `(+n)` after children=" in rendered.text, "and the legend explains the marker"


def test_the_root_line_carries_its_own_count_and_marker():
    """The window's own number is the one `java_get_element` prints for it."""
    rendered = render(window_walk(root_children=5), max_nodes=50, only_interactive=False,
                      fields=("children",))

    # One top-level node was walked against five the element reports.
    assert 'window frame "Window" children=5 (+4)' in rendered.text.splitlines()[0]


def test_nothing_is_added_when_the_two_counts_agree():
    """The common case must not grow a paragraph."""
    records = (
        (0, {"role": "panel", "name": "Ordinary container", "children_count": 1,
             "index_in_parent": 0, "object_depth": 1, "states": "", "description": ""}),
        (1, {"role": "push button", "name": "Child", "children_count": 0,
             "index_in_parent": 0, "object_depth": 2, "states": "", "description": ""}),
    )
    root = Record.from_pyjab({
        "role": "frame", "name": "Window", "children_count": 1,
        "index_in_parent": 0, "object_depth": 0, "states": "", "description": "",
    })
    rendered = render(Walk(root=root, nodes=walked_nodes(records), walked=2),
                      max_nodes=50, only_interactive=False, fields=("children",))

    assert "(+" not in rendered.text
    assert "A `(+n)`" not in rendered.text


def test_the_legend_stays_away_when_the_count_is_not_printed():
    """A legend explaining a marker nobody can see is noise, and tokens are the budget."""
    rendered = render(window_walk(), max_nodes=50, only_interactive=False)

    assert "children=" not in rendered.text
    assert "A `(+n)`" not in rendered.text


def test_this_projects_own_filter_does_not_look_like_an_invisible_child():
    """A node dropped by `only_interactive` was still *walked*: that is our rendering, not
    pyjab's gap, and the reply must not blame the application for it."""
    # Unnamed containers with nothing interactive inside them: what the filter exists to drop.
    records = (
        (0, {"role": "panel", "name": "", "children_count": 1,
             "index_in_parent": 0, "object_depth": 1, "states": "", "description": ""}),
        (1, {"role": "panel", "name": "", "children_count": 0,
             "index_in_parent": 0, "object_depth": 2, "states": "", "description": ""}),
    )
    root = Record.from_pyjab({
        "role": "frame", "name": "Window", "children_count": 1,
        "index_in_parent": 0, "object_depth": 0, "states": "", "description": "",
    })
    walk = Walk(root=root, nodes=walked_nodes(records), walked=2)

    filtered = render(walk, max_nodes=50, only_interactive=True, fields=("children",))
    unfiltered = render(walk, max_nodes=50, only_interactive=False, fields=("children",))

    assert "panel" not in filtered.text, "the unnamed containers really are dropped"
    assert "(+" not in filtered.text, (
        "the walk produced both of them, so nothing is missing: our filter is not a gap in "
        "the accessibility tree and must not be reported as one"
    )
    assert "(+" not in unfiltered.text, "and the counts agree when everything is rendered"


def test_a_truncated_walk_says_the_gap_may_be_its_limit():
    """Two reasons for one marker, and the reply must not pick one of them for the reader."""
    walk = window_walk()
    truncated = Walk(root=walk.root, nodes=walk.nodes, walked=walk.walked,
                     truncated=True, reason="raw walk cap reached (4 records)")

    rendered = render(truncated, max_nodes=50, only_interactive=False, fields=("children",))

    assert "may be its limit" in rendered.text
    assert "truncated:" in rendered.text, "and the truncation notice is still there"
