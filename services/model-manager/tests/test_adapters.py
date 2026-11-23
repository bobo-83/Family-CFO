"""LocalHardwareSource node-memory evidence: the operator-declared seam.

The swap scripts suite runs the manager as a subprocess, so its admission
evidence must not depend on the runner's RAM (issue #180: a ~7 GB macOS CI
runner refused every synthetic model at the 8 GiB reserve floor). Here the
probe seam is patched explicitly; nothing asserts which real probe the host
happens to have.
"""

from __future__ import annotations

import pytest

from family_cfo_model_manager import adapters
from family_cfo_model_manager.adapters import LocalHardwareSource
from family_cfo_model_manager.contracts import Evidence, ReasonCode
from family_cfo_model_manager.planning import PlacementDemand, node_memory_verdict


@pytest.fixture(autouse=True)
def clean_seam(monkeypatch):
    monkeypatch.delenv("FAMILY_CFO_NODE_MEMORY_GB", raising=False)


def test_declared_memory_takes_precedence_over_probe(monkeypatch):
    monkeypatch.setenv("FAMILY_CFO_NODE_MEMORY_GB", "64")
    monkeypatch.setattr(adapters, "_meminfo_gb", lambda: 7.0)
    node = LocalHardwareSource().nodes()[0]
    assert node.memory_gb == 64.0
    # Declared, not measured - never classified as VERIFIED.
    assert node.evidence is Evidence.ESTIMATED


def test_measured_probe_used_without_declaration(monkeypatch):
    monkeypatch.setattr(adapters, "_meminfo_gb", lambda: 32.0)
    node = LocalHardwareSource().nodes()[0]
    assert node.memory_gb == 32.0
    assert node.evidence is Evidence.VERIFIED


def test_undeclared_value_falls_back_to_probe(monkeypatch):
    monkeypatch.setenv("FAMILY_CFO_NODE_MEMORY_GB", "not-a-number")
    monkeypatch.setattr(adapters, "_meminfo_gb", lambda: None)
    node = LocalHardwareSource().nodes()[0]
    assert node.memory_gb is None
    assert node.evidence is Evidence.UNKNOWN


def test_small_declared_memory_refuses_through_seam(monkeypatch):
    """Refusal works through the seam: a demand inside the suite's 64 GiB
    declaration is refused when the seam declares a small pool."""
    demand = PlacementDemand(
        weights_gb=1.0, overhead_gb=0.5, kv_gb=0.5, total_gb=2.0
    )
    monkeypatch.setenv("FAMILY_CFO_NODE_MEMORY_GB", "3")
    fits, reason = node_memory_verdict(LocalHardwareSource().nodes()[0], demand)
    assert not fits
    assert reason == ReasonCode.INSUFFICIENT_MEMORY.value
    monkeypatch.setenv("FAMILY_CFO_NODE_MEMORY_GB", "64")
    fits, reason = node_memory_verdict(LocalHardwareSource().nodes()[0], demand)
    assert (fits, reason) == (True, None)
