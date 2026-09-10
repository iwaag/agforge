"""forge cannot reach Plane, as a property of the import graph.

A rejecting client proves the calls fail; this proves there are none to make.
`refactor` p1 asserted the same of autolab, and for the same reason: an agent
that says it no longer uses a system should be unable to, not merely
configured not to.
"""

import subprocess
import sys


MODULES = (
    "agforge.zulip_listener",
    "agforge.assetplan_topic",
    "agforge.assetrun_topic",
    "agforge.record",
    "agforge.entrance_topic",
    "agforge.request_service",
    "agforge.cli",
    "agforge.intro",
    "agforge.retire",
)


def test_no_forge_module_loads_the_plane_client():
    for name in MODULES:
        __import__(name)
    assert [module for module in sys.modules if module.startswith("agag.plane")] == []


def test_the_whole_package_loads_without_plane_in_a_fresh_interpreter():
    """In its own process, so nothing another test imported can hide it."""
    program = (
        "import importlib, sys;"
        f"[importlib.import_module(name) for name in {MODULES!r}];"
        "print([m for m in sys.modules if m.startswith('agag.plane')])"
    )
    result = subprocess.run([sys.executable, "-c", program],
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"


def test_forge_keeps_no_plane_credential_path():
    """The credential is gone from the code, so nothing can be pointed at it."""
    import agforge.record as record

    assert not any("plane" in name.lower() for name in dir(record))
