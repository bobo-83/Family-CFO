"""Typed model-manager v2 transport (issue #170, ADR 0081).

The API never talks Docker and never falls back to the legacy `/swap`
mutation surface: this client speaks protocol major 2 only, verifies the
manager's protocol before any mutation, carries the deployment-scoped
internal credential on every v2 mutator, and converts manager errors into
the stable public vocabulary from spec 04. Transport-ambiguous failures
raise so callers can apply the request-identity retry discipline — a
severed apply is safe because the manager replays by `request_id`.

Timeouts are the explicit transport budget chain (plan `Detailed design` 1):
the admission call carries the dedicated ~190 s apply budget from
`config.py` (NOT the shared `_HF_TIMEOUT_SECONDS`), and every read uses a
short bounded budget so a hung sidecar cannot stall a poll loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

PROTOCOL_MAJOR = 2
_CREDENTIAL_HEADER = "x-manager-credential"
# The error envelope every manager error carries: {"error": {"code", "message"}}.
_MAX_ERROR_MESSAGE = 200


class ManagerTransportError(Exception):
    """The manager could not be reached or answered outside its envelope."""


class ManagerProtocolError(Exception):
    """The manager does not speak protocol major 2 (mixed pair: fail safely)."""


class ManagerJobError(Exception):
    """A structured manager refusal/failure with a stable code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ManagerJobStatus:
    """The v2 job projection (manager `_status_document`).

    `state` is the raw lifecycle value; `coarse_state` is the additive
    legacy projection shipped clients decode (ADR 0074 Decision 5).
    """

    job_id: str | None
    request_id: str | None
    sequence: int | None
    state: str
    decision: str
    origin: str
    requested_selection: dict[str, Any] | None
    resolved_plan: dict[str, Any] | None
    previous_selection: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None
    deadline_at: str | None
    reason_code: str | None
    message: str | None
    recovery_available: bool
    runtime_key: str | None = None
    target_plan_hash: str | None = None
    initiator_household_id: str | None = None
    initiator_user_id: str | None = None
    verified_at: str | None = None

    @property
    def coarse_state(self) -> str:
        """The legacy idle/running/succeeded/failed projection (spec 04).

        Mirrors the manager's own `project_legacy_state`: in-flight states
        (including commit_repair_required, which blocks new applies but keeps
        the deployment transaction open) project to running;
        restored/recovery_failed project to failed.
        """
        if self.state == "succeeded":
            return "succeeded"
        if self.state in ("failed", "restored", "recovery_failed"):
            return "failed"
        if self.state in (
            "accepted",
            "staging",
            "launching",
            "verifying",
            "commit_pending",
            "restoring",
            "commit_repair_required",
        ):
            return "running"
        raise ValueError(f"unprojectable job state: {self.state!r}")

    @property
    def is_unfinished(self) -> bool:
        """Whether a replacement transaction is in flight (PUT exclusion)."""
        return self.coarse_state == "running"

    @property
    def evidence_age_seconds(self) -> float | None:
        """Age of the last successful verification, or None when unknown."""
        if not self.verified_at:
            return None
        try:
            from datetime import UTC, datetime

            verified = datetime.fromisoformat(self.verified_at)
        except ValueError:
            return None
        if verified.tzinfo is None:
            verified = verified.replace(tzinfo=UTC)
        return max(0.0, datetime.now(UTC).timestamp() - verified.timestamp())


def _job_status_from_document(document: dict[str, Any]) -> ManagerJobStatus:
    return ManagerJobStatus(
        job_id=document.get("job_id"),
        request_id=document.get("request_id"),
        sequence=document.get("sequence"),
        state=document.get("state") or "",
        decision=document.get("decision") or "undecided",
        origin=document.get("origin") or "api",
        requested_selection=document.get("requested_selection"),
        resolved_plan=document.get("resolved_plan"),
        previous_selection=document.get("previous_selection"),
        created_at=document.get("created_at"),
        updated_at=document.get("updated_at"),
        deadline_at=document.get("deadline_at"),
        reason_code=document.get("reason_code"),
        message=document.get("message"),
        recovery_available=bool(document.get("recovery_available", False)),
        runtime_key=document.get("runtime_key"),
        target_plan_hash=document.get("target_plan_hash"),
        initiator_household_id=document.get("initiator_household_id"),
        initiator_user_id=document.get("initiator_user_id"),
        verified_at=document.get("verified_at"),
    )


class ModelManagerClient:
    """One typed transport for the manager v2 surface. No legacy fallback."""

    def __init__(
        self,
        base_url: str,
        credential: str,
        *,
        apply_timeout_seconds: float = 190.0,
        read_timeout_seconds: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.credential = credential.strip()
        self.apply_timeout_seconds = apply_timeout_seconds
        self.read_timeout_seconds = read_timeout_seconds

    # --- protocol ----------------------------------------------------------------

    def verify_protocol(self) -> dict[str, Any]:
        """GET /v2/capabilities and require protocol major 2 (ADR 0081 S6).

        A manager without the v2 surface (404) or with a different major is a
        mixed pair: mutation must fail safely, so this raises
        ManagerProtocolError which callers surface as
        `manager_upgrade_required` — never a legacy fallback.
        """
        capabilities = self._request("GET", "/v2/capabilities", read=True)
        if capabilities.status_code == 404:
            raise ManagerProtocolError("the model manager does not expose the v2 protocol surface")
        if capabilities.status_code >= 400:
            raise ManagerTransportError(
                f"capabilities check failed with status {capabilities.status_code}"
            )
        try:
            document = capabilities.json()
            major = int(document["protocol_major"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ManagerTransportError("malformed capabilities document") from exc
        if major != PROTOCOL_MAJOR:
            raise ManagerProtocolError(
                f"the model manager speaks protocol major {major}; this API requires "
                f"major {PROTOCOL_MAJOR} — upgrade the manager sidecar"
            )
        return document

    # --- reads -------------------------------------------------------------------

    def current_job(self) -> ManagerJobStatus | None:
        """The current/latest job, or None when the manager reports none."""
        response = self._request("GET", "/v2/jobs/current", read=True)
        if response.status_code == 404:
            return None
        return _job_status_from_document(self._job_document(response, "current job"))

    def job(self, job_id: str) -> ManagerJobStatus:
        """One retained job by id; a 404 names an unknown retained job."""
        response = self._request("GET", f"/v2/jobs/{job_id}", read=True)
        return _job_status_from_document(self._job_document(response, "job"))

    # --- mutations (deployment-scoped credential) ---------------------------------

    def admit(self, admission: dict[str, Any]) -> ManagerJobStatus:
        """POST /v2/jobs — one-shot admission; the 180 s preflight runs here."""
        response = self._request("POST", "/v2/jobs", json_body=admission, read=False)
        return _job_status_from_document(self._job_document(response, "admission"))

    def decide(self, job_id: str, decision: str) -> ManagerJobStatus:
        response = self._request(
            "POST",
            f"/v2/jobs/{job_id}/decision",
            json_body={"decision": decision},
            read=True,
        )
        return _job_status_from_document(self._job_document(response, "decision"))

    def ack(self, job_id: str, *, receipt_job_id: str, plan_hash: str) -> ManagerJobStatus:
        response = self._request(
            "POST",
            f"/v2/jobs/{job_id}/ack",
            json_body={
                "job_id": job_id,
                "receipt_job_id": receipt_job_id,
                "plan_hash": plan_hash,
            },
            read=True,
        )
        return _job_status_from_document(self._job_document(response, "acknowledgement"))

    def recover(self, job_id: str) -> ManagerJobStatus:
        """Re-run the frozen direction of the durable decision."""
        response = self._request("POST", f"/v2/jobs/{job_id}/recover", json_body=None, read=True)
        return _job_status_from_document(self._job_document(response, "recovery"))

    # --- internals ----------------------------------------------------------------

    def _job_document(self, response: httpx.Response, what: str) -> dict[str, Any]:
        if response.status_code >= 400:
            self._raise_manager_error(response)
        try:
            document = response.json()
        except ValueError as exc:
            raise ManagerTransportError(f"malformed {what} document") from exc
        if not isinstance(document, dict):
            raise ManagerTransportError(f"malformed {what} document")
        return document

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        read: bool,
    ) -> httpx.Response:
        # The credential rides on every call when configured: v2 mutators
        # require it (recover sends no body) and reads ignore it.
        headers: dict[str, str] = {}
        if self.credential:
            headers[_CREDENTIAL_HEADER] = self.credential
        try:
            return httpx.request(
                method,
                f"{self.base_url}{path}",
                json=json_body,
                headers=headers,
                timeout=(self.read_timeout_seconds if read else self.apply_timeout_seconds),
            )
        except httpx.HTTPError as exc:
            raise ManagerTransportError(
                f"model manager unreachable ({exc.__class__.__name__})"
            ) from exc

    def _raise_manager_error(self, response: httpx.Response) -> None:
        code = "manager_error"
        message = f"model manager returned status {response.status_code}"
        try:
            envelope = response.json()
            error = envelope.get("error") or {}
            code = str(error.get("code") or code)
            message = str(error.get("message") or message)[:_MAX_ERROR_MESSAGE]
        except ValueError:
            pass
        raise ManagerJobError(response.status_code, code, message)


def manager_unavailable_details(exc: Exception) -> dict[str, str]:
    """Safe details for a 503 envelope: the stable cause, nothing more."""
    if isinstance(exc, ManagerJobError):
        return {"manager_code": exc.code}
    return {}


def monotonic() -> float:
    """Indirection for the PUT-guard cache clock (tests inject)."""
    return time.monotonic()
