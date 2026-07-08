from family_cfo_api.tools.openapi import (
    build_openapi,
    check_implemented_routes,
    load_shared_openapi,
)


def test_implemented_routes_match_shared_openapi_contract() -> None:
    errors = check_implemented_routes(
        generated_spec=build_openapi(),
        shared_spec=load_shared_openapi(),
    )

    assert errors == []
