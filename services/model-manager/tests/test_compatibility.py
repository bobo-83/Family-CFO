"""Prior API against the current model-manager wire contract (ADR 0074)."""

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from family_cfo_model_manager import main as manager

_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "shared/schemas/model-manager-v1-contract.json").read_text()
)["operations"]


def _assert_previous_shape(actual, previous) -> None:
    """Current responses may add fields but must preserve old fields and types."""
    if isinstance(previous, dict):
        assert isinstance(actual, dict)
        for key, value in previous.items():
            assert key in actual
            _assert_previous_shape(actual[key], value)
    elif previous is not None:
        assert type(actual) is type(previous)


def test_current_manager_accepts_previous_api_contract() -> None:
    client = TestClient(manager.app)

    swap = _FIXTURE["swap"]
    manager._status = manager.SwapStatus(state="idle")
    with patch.object(manager.threading, "Thread"):
        response = client.request(swap["method"], swap["path"], json=swap["request"])
    assert response.status_code == 202
    _assert_previous_shape(response.json(), swap["response"])

    status = _FIXTURE["status"]
    manager._status = manager.SwapStatus(**status["response"])
    response = client.request(status["method"], status["path"])
    assert response.status_code == 200
    _assert_previous_shape(response.json(), status["response"])

    logs = _FIXTURE["logs"]
    process = type(
        "Result",
        (),
        {"returncode": 0, "stdout": logs["response"]["lines"], "stderr": ""},
    )()
    with patch.object(manager.subprocess, "run", return_value=process):
        response = client.request(logs["method"], logs["path"], params=logs["query"])
    assert response.status_code == 200
    _assert_previous_shape(response.json(), logs["response"])
