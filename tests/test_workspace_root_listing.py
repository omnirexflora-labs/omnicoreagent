"""Listing the workspace root with "." is allowed, like listing with no path.

Found writing the Harbor page (D8): the model's first call in a live trial,
`ls(path=".")`, was rejected as invalid arguments: "." normalized to an empty
path, which a target refuses. For the listing and search tools the root is
the whole workspace, as when no path is given; a write to "." stays an error.
"""

from __future__ import annotations

import pytest

from omnicoreagent.governance.capabilities import tool_authority_requests


def _requests(tool_name, args):
    return tool_authority_requests(
        tool_name=tool_name, tool_args=args, tool_provider="workspace",
        tool_server=None, actor="a", tool_call_id="c",
    )


@pytest.mark.parametrize("tool_name", ["ls", "glob", "grep"])
@pytest.mark.parametrize("root", [".", "./", "/"])
def test_the_root_is_the_whole_workspace_for_listing_and_search(tool_name, root):
    args = {"path": root, "pattern": "*"}
    (with_root,) = _requests(tool_name, args)
    (without,) = _requests(tool_name, {"pattern": "*"})
    assert with_root.capability == without.capability
    assert with_root.target == without.target


def test_a_write_to_the_root_is_still_refused():
    with pytest.raises(ValueError):
        _requests("write_file", {"path": ".", "content": "x"})
