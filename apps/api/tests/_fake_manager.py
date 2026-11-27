"""An in-process model-manager v2 surface for API tests (issue #170).

Mirrors the real sidecar's routes, envelopes, and credential enforcement
(`services/model-manager`) and is served through the module-level
`httpx.request` the typed client uses, so tests drive the REAL
`ModelManagerClient` transport -- header name, error-envelope parsing, 404
semantics -- instead of mocking the client away.

Scripting model: admission creates a durable job document exactly like the
real journal would; tests then move it through lifecycle states with
`set_state`, or set `admission_refusal` / `protocol_major` to exercise the
refusal and mixed-pair paths.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

CREDENTIAL_HEADER = "x-manager-credential"
DEFAULT_CREDENTIAL = "test-deployment-credential"


def _resolve_photo_mode(requested: str, vision_model: str | None) -> str:
    """Mirror the manager's admission-time photo_mode resolution."""
    if requested == "auto":
        return "separate" if vision_model else "off"
    return requested


class FakeManagerV2:
    """Route-for-route fake of the manager's v2 surface."""

    def __init__(
        self,
        *,
        protocol_major: int | None = 2,
        credential: str | None = DEFAULT_CREDENTIAL,
    ) -> None:
        self.protocol_major = protocol_major
        self.credential = credential
        self.jobs: dict[str, dict[str, Any]] = {}
        self.order: list[str] = []
        self.calls: list[tuple[str, str, Any]] = []
        # (status, code, message) raised by POST /v2/jobs, mirroring an
        # ExecutorRefusal (e.g. swap_in_progress / model_not_found).
        self.admission_refusal: tuple[int, str, str] | None = None
        # When set, every request raises ConnectError (manager unreachable).
        self.unreachable = False
        # (status, code, message) raised by POST /v2/jobs/{id}/recover,
        # mirroring a recovery refusal (e.g. request_id_conflict).
        self.recovery_refusal: tuple[int, str, str] | None = None

    # --- scripting ----------------------------------------------------------------

    def set_state(self, job_id: str, state: str, **fields: Any) -> None:
        """Move a job through its lifecycle (verified_at is the evidence clock)."""
        document = self.jobs[job_id]
        document["state"] = state
        document.update(fields)

    def seed_job(
        self,
        *,
        state: str = "verifying",
        decision: str = "undecided",
        **fields: Any,
    ) -> str:
        """Seed a job document directly (bypassing admission). Returns the id."""
        job_id = str(uuid.uuid4())
        document = {
            "job_id": job_id,
            "request_id": str(uuid.uuid4()),
            "sequence": len(self.order) + 1,
            "state": state,
            "decision": decision,
            "origin": "api",
            "requested_selection": None,
            "resolved_plan": None,
            "previous_selection": None,
            "created_at": "2026-11-21T12:00:00+00:00",
            "updated_at": "2026-11-21T12:00:00+00:00",
            "deadline_at": None,
            "reason_code": None,
            "message": None,
            "recovery_available": False,
            "runtime_key": "vllm+http://vllm:8000",
            "target_plan_hash": "ab" * 32,
            "initiator_household_id": None,
            "initiator_user_id": None,
            "verified_at": None,
        }
        document.update(fields)
        self.jobs[job_id] = document
        self.order.append(job_id)
        return job_id

    @property
    def admission_calls(self) -> list[dict[str, Any]]:
        """The POST /v2/jobs bodies, in order (excludes decision/ack/recover)."""
        return [
            body
            for method, url, body in self.calls
            if method == "POST" and body and url.endswith("/v2/jobs")
        ]

    def _response(self, method: str, url: str, status_code: int, content: Any) -> httpx.Response:
        return httpx.Response(status_code, json=content, request=httpx.Request(method, url))

    def _error(self, method: str, url: str, status_code: int, code: str, message: str):
        return self._response(
            method, url, status_code, {"error": {"code": code, "message": message}}
        )

    # --- transport ------------------------------------------------------------------

    def handle(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError("model manager down", request=httpx.Request(method, url))
        headers = kwargs.get("headers") or {}
        body = kwargs.get("json")
        self.calls.append((method, url, body))
        path = httpx.URL(url).path

        if path == "/v2/capabilities":
            if self.protocol_major is None:
                # A pre-v2 manager serves no such route: FastAPI's plain 404.
                return self._response(method, url, 404, {"detail": "Not Found"})
            return self._response(
                method,
                url,
                200,
                {"protocol_major": self.protocol_major, "build": "0", "journal_schema": 2},
            )

        if path == "/v2/jobs" and method == "POST":
            return self._admit(method, url, headers, body)

        if path == "/v2/jobs/current" and method == "GET":
            if not self.order:
                return self._error(method, url, 404, "unknown_job", "no job has been admitted")
            return self._response(method, url, 200, self.jobs[self.order[-1]])

        if path.startswith("/v2/jobs/"):
            job_id = path.removeprefix("/v2/jobs/").split("/")[0]
            action = path.removeprefix(f"/v2/jobs/{job_id}")
            document = self.jobs.get(job_id)
            if document is None:
                return self._error(method, url, 404, "unknown_job", "no retained job with that id")
            error = self._mutator_guard(method, url, headers)
            if error is not None:
                return error
            if action == "/decision":
                document["decision"] = (body or {}).get("decision", document["decision"])
                return self._response(method, url, 200, document)
            if action == "/ack":
                document["state"] = "succeeded"
                document["message"] = None
                return self._response(method, url, 200, document)
            if action == "/recover":
                if self.recovery_refusal is not None:
                    status_code, code, message = self.recovery_refusal
                    return self._error(method, url, status_code, code, message)
                document["state"] = "restoring"
                return self._response(method, url, 200, document)
            if action == "" and method == "GET":
                return self._response(method, url, 200, document)

        return self._response(method, url, 404, {"detail": "Not Found"})

    def _mutator_guard(
        self, method: str, url: str, headers: dict[str, str]
    ) -> httpx.Response | None:
        """Mutators require the deployment credential; unconfigured fails closed."""
        if method != "POST":
            return None
        expected = (self.credential or "").strip()
        provided = (headers.get(CREDENTIAL_HEADER) or "").strip()
        if not expected or not provided or provided != expected:
            return self._error(
                method,
                url,
                401,
                "unauthorized",
                "a valid internal credential is required",
            )
        return None

    def _admit(
        self, method: str, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> httpx.Response:
        error = self._mutator_guard(method, url, headers)
        if error is not None:
            return error
        if self.admission_refusal is not None:
            status_code, code, message = self.admission_refusal
            return self._error(method, url, status_code, code, message)
        try:
            request_id = str(uuid.UUID(str(body.get("request_id"))))
        except (ValueError, AttributeError, TypeError):
            return self._error(method, url, 422, "invalid_model_id", "request_id must be a UUID")
        # Replay by request identity: the manager answers a repeated request_id
        # with the SAME job (a read, never a second launch).
        for existing in self.jobs.values():
            if existing["request_id"] == request_id:
                return self._response(method, url, 202, existing)
        job_id = str(uuid.uuid4())
        document = {
            "job_id": job_id,
            "request_id": request_id,
            "sequence": len(self.order) + 1,
            "state": "accepted",
            "decision": "undecided",
            "origin": body.get("origin") or "api",
            "requested_selection": {
                "main_model": body.get("main_model"),
                "vision_model": body.get("vision_model"),
                # The manager resolves the selection's photo_mode at admission
                # (never AUTO once a plan exists): auto + a distinct vision id
                # is separate; without one the conservative resolution is off.
                "photo_mode": _resolve_photo_mode(
                    body.get("photo_mode") or "auto", body.get("vision_model")
                ),
                "topology": body.get("topology") or "auto",
            },
            "resolved_plan": None,
            "previous_selection": None,
            "created_at": "2026-11-21T12:00:00+00:00",
            "updated_at": "2026-11-21T12:00:00+00:00",
            "deadline_at": None,
            "reason_code": None,
            "message": None,
            "recovery_available": False,
            "runtime_key": body.get("runtime_key"),
            "target_plan_hash": "ab" * 32,
            "initiator_household_id": body.get("initiator_household_id"),
            "initiator_user_id": body.get("initiator_user_id"),
            "verified_at": None,
        }
        self.jobs[job_id] = document
        self.order.append(job_id)
        return self._response(method, url, 202, document)

    def transport(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        return self.handle(method, url, **kwargs)
