"""#92: the 429 contract.

The server has always sent `Retry-After`, but the hand-maintained contract
described every 429 as a plain error, so neither generated client exposed the
header and both printed a guess — "wait a minute" against a fifteen-minute
lockout. These tests guard the shape the clients now depend on: a 429 a person
can hit is documented, and it is documented as carrying the wait.
"""

from __future__ import annotations

from typing import Any

from family_cfo_api.tools.openapi import HTTP_METHODS, build_openapi, load_shared_openapi

RATE_LIMITED_REF = "#/components/responses/RateLimited"

# Every 429 a person can walk into on a sign-in-shaped path. The chat quota
# (off by default) and the backup cooldown are deliberately not here: their
# bodies already name the wait, and they are in-app actions rather than a
# locked door.
USER_FACING_429_OPERATIONS = {
    ("post", "/auth/sessions"),  # web login
    ("post", "/pairing/login"),  # iOS login
    ("post", "/invites/preview"),  # public join page, on load
    ("post", "/invites/accept"),  # public join page, on submit
    ("post", "/auth/password"),  # #97: the change-password form's re-auth
}


def _operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, operation)
        for path, path_item in spec.get("paths", {}).items()
        for method, operation in path_item.items()
        if method in HTTP_METHODS and isinstance(operation, dict)
    ]


def test_rate_limited_response_documents_retry_after() -> None:
    header = load_shared_openapi()["components"]["responses"]["RateLimited"]["headers"][
        "Retry-After"
    ]
    # Optional and string-typed on purpose: a proxy may strip or mangle it, and
    # a client must degrade to "later" rather than fail the whole response.
    assert header.get("required") is not True
    assert header["schema"]["type"] == "string"


def test_every_documented_429_is_the_rate_limited_response() -> None:
    shared = load_shared_openapi()
    for method, path, operation in _operations(shared):
        response = operation.get("responses", {}).get("429")
        if response is None:
            continue
        assert response == {"$ref": RATE_LIMITED_REF}, (
            f"{method.upper()} {path}: a 429 must document Retry-After, "
            "otherwise the generated clients drop it (#92)"
        )


def test_the_429s_a_person_walks_into_are_documented() -> None:
    shared = load_shared_openapi()
    documented = {
        (method, path)
        for method, path, operation in _operations(shared)
        if "429" in operation.get("responses", {})
    }
    assert USER_FACING_429_OPERATIONS <= documented


def test_the_implementation_declares_those_429s_too() -> None:
    # Belt and braces around `make check-openapi`: the contract may only
    # promise a 429 the app actually declares, or the generated clients would
    # carry a case the server never produces.
    generated = {
        (method, path.removeprefix("/api/v1"))
        for method, path, operation in _operations(build_openapi())
        if "429" in operation.get("responses", {})
    }
    assert USER_FACING_429_OPERATIONS <= generated
