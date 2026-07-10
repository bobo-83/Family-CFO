"""Model-manager sidecar (ADR 0013).

The ONE privileged service in the stack: it mounts the Docker socket and the
project directory, and exposes exactly one mutating operation — swap the served
models by running the repo's `scripts/swap-model.sh`. It never executes caller-
supplied commands; inputs are strict Hugging Face repo ids. Internal network
only; the API (owner-gated) is the only caller.
"""

from __future__ import annotations

import re
import subprocess
import threading
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

PROJECT_DIR = "/project"
SWAP_SCRIPT = "scripts/swap-model.sh"
SWAP_TIMEOUT_SECONDS = 1800  # .env update + container recreation; downloads continue async
LOG_TAIL_CHARS = 4000

# org/name with a conservative charset — the only shape ever passed to the shell.
_REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


class SwapRequest(BaseModel):
    main_model: str
    vision_model: str | None = None  # None -> disable photo analysis ("none")


class SwapStatus(BaseModel):
    state: Literal["idle", "running", "succeeded", "failed"]
    main_model: str | None = None
    vision_model: str | None = None
    log_tail: str = ""


app = FastAPI(title="Family CFO Model Manager")

_lock = threading.Lock()
_status = SwapStatus(state="idle")


def _validate_repo_id(value: str) -> None:
    if not _REPO_ID.match(value):
        raise HTTPException(status_code=422, detail=f"invalid model id: {value!r}")


def _run_swap(main_model: str, vision_model: str | None) -> None:
    global _status
    args = ["bash", SWAP_SCRIPT, main_model]
    # A vision-capable main takes no second arg (swap-model.sh rejects it).
    if not _is_vision_model(main_model):
        args.append(vision_model if vision_model else "none")
    try:
        result = subprocess.run(
            args,
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=SWAP_TIMEOUT_SECONDS,
        )
        output = (result.stdout + "\n" + result.stderr)[-LOG_TAIL_CHARS:]
        state = "succeeded" if result.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        output, state = "swap timed out", "failed"
    with _lock:
        _status = SwapStatus(
            state=state, main_model=main_model, vision_model=vision_model, log_tail=output
        )


def _is_vision_model(model_id: str) -> bool:
    return "-vl-" in model_id.lower()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# M50: read-only container log access so the API can explain what "loading"
# actually means (downloading / loading weights / crashed). Allowlisted
# service names only — never caller-supplied strings.
_LOG_SERVICES = {"vllm", "vllm-vision"}


@app.get("/logs")
def logs(service: str, tail: int = 30) -> dict:
    if service not in _LOG_SERVICES:
        raise HTTPException(status_code=422, detail="unknown service")
    tail = max(1, min(tail, 200))
    try:
        result = subprocess.run(
            ["docker", "compose", "logs", "--no-color", "--tail", str(tail), service],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=503, detail="log read timed out") from exc
    if result.returncode != 0:
        raise HTTPException(status_code=503, detail="log read failed")
    return {"lines": result.stdout[-LOG_TAIL_CHARS:]}


@app.get("/status", response_model=SwapStatus)
def status() -> SwapStatus:
    with _lock:
        return _status


@app.post("/swap", response_model=SwapStatus, status_code=202)
def swap(payload: SwapRequest) -> SwapStatus:
    global _status
    _validate_repo_id(payload.main_model)
    if payload.vision_model is not None:
        _validate_repo_id(payload.vision_model)

    with _lock:
        if _status.state == "running":
            raise HTTPException(status_code=409, detail="a swap is already in progress")
        _status = SwapStatus(
            state="running", main_model=payload.main_model, vision_model=payload.vision_model
        )

    thread = threading.Thread(
        target=_run_swap, args=(payload.main_model, payload.vision_model), daemon=True
    )
    thread.start()
    with _lock:
        return _status
