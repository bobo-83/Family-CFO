from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from family_cfo_api.main import create_app
from family_cfo_api.openapi_compat import SWIFT_GENERATOR_NULLABLE_COMPONENTS

HTTP_METHODS = {"delete", "get", "patch", "post", "put"}
# Enforce nullability symmetrically inside the coordinated Item 2 response
# families while retaining the historical one-way rule for legacy components.
STRICT_NULLABILITY_COMPONENTS = SWIFT_GENERATOR_NULLABLE_COMPONENTS
STRICT_REQUEST_BODY_OPERATIONS = frozenset(
    {
        "PUT /backups/config",
        "POST /backups/destination-check",
        "POST /backups/remote/restore",
        "POST /backups/remote/delete",
    }
)
STRICT_REQUIRED_COMPONENTS = frozenset(
    {
        "BackupCapacityObservation",
        "BackupConfig",
        "BackupConfigUpdateRequest",
        "BackupDestinationCheckRequest",
        "BackupDestinationCheckResponse",
        "BackupDestinationRecoveryStatus",
        "BackupJob",
        "BackupRecoveryStatus",
        "BackupRetentionPolicy",
        "BackupRetentionPolicyUpdate",
        "RemoteBackup",
        "RemoteBackupListResponse",
        "RemoteRestoreRequest",
    }
)
REPO_ROOT = Path(__file__).resolve().parents[5]
SHARED_OPENAPI = REPO_ROOT / "shared" / "openapi" / "family-cfo.v1.yaml"


def build_openapi() -> dict[str, Any]:
    return create_app().openapi()


def load_shared_openapi(path: Path = SHARED_OPENAPI) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def _server_prefix(shared_spec: dict[str, Any]) -> str:
    servers = shared_spec.get("servers", [])
    if not servers:
        return ""

    url = servers[0].get("url", "")
    return "" if url == "/" else url.rstrip("/")


def _normalize_generated_path(path: str, prefix: str) -> str:
    if prefix and path.startswith(prefix):
        normalized = path.removeprefix(prefix)
        return normalized or "/"

    return path


def _operation_items(path_item: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (method, operation)
        for method, operation in path_item.items()
        if method in HTTP_METHODS and isinstance(operation, dict)
    ]


def _schema_ref_name(schema: dict[str, Any]) -> str | None:
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return None

    return ref.rsplit("/", maxsplit=1)[-1]


def _allows_null(
    schema: dict[str, Any],
    spec: dict[str, Any],
    visited: set[str] | None = None,
) -> bool:
    schema_type = schema.get("type")
    if schema_type == "null" or (isinstance(schema_type, list) and "null" in schema_type):
        return True

    ref_name = _schema_ref_name(schema)
    if ref_name:
        visited = visited or set()
        if ref_name in visited:
            return False
        visited.add(ref_name)
        if _allows_null(_component(spec, ref_name), spec, visited):
            return True

    return any(
        isinstance(option, dict) and _allows_null(option, spec, set(visited or ()))
        for keyword in ("anyOf", "oneOf")
        for option in schema.get(keyword, [])
    )


def _without_null(schema: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize OpenAPI's equivalent nullable spellings for comparison."""
    normalized = dict(schema)
    schema_type = normalized.get("type")
    if isinstance(schema_type, list):
        non_null = [item for item in schema_type if item != "null"]
        normalized["type"] = non_null[0] if len(non_null) == 1 else non_null
    for keyword in ("anyOf", "oneOf"):
        if keyword not in normalized:
            continue
        options = [
            option
            for option in normalized[keyword]
            if not (isinstance(option, dict) and option.get("type") == "null")
        ]
        if len(options) == 1:
            preserved = {
                key: value for key, value in normalized.items() if key not in {keyword, "type"}
            }
            preserved.update(options[0])
            normalized = preserved
        else:
            normalized[keyword] = options
    all_of = normalized.get("allOf")
    if isinstance(all_of, list) and len(all_of) == 1:
        preserved = {key: value for key, value in normalized.items() if key != "allOf"}
        preserved.update(all_of[0])
        normalized = preserved
    return normalized


def _component(spec: dict[str, Any], name: str) -> dict[str, Any]:
    return spec.get("components", {}).get("schemas", {}).get(name, {})


def _compare_schema_recursive(
    generated_spec: dict[str, Any],
    shared_spec: dict[str, Any],
    generated_schema: dict[str, Any],
    shared_schema: dict[str, Any],
    location: str,
    visited: set[tuple[str, str]],
) -> list[str]:
    errors: list[str] = []
    # The shared contract is authoritative in both directions: generated
    # responses must neither remove promised nullability nor introduce null
    # where clients were promised a concrete value.
    generated_nullable = _allows_null(generated_schema, generated_spec)
    shared_nullable = _allows_null(shared_schema, shared_spec)
    current_component = location.rsplit(" -> ", maxsplit=1)[-1]
    current_component = current_component.split(".", maxsplit=1)[0].removesuffix("[]")
    if shared_nullable and not generated_nullable:
        errors.append(f"{location}: generated schema is missing contract nullability")
    elif (
        generated_nullable
        and not shared_nullable
        and current_component in STRICT_NULLABILITY_COMPONENTS
    ):
        errors.append(f"{location}: generated schema adds nullability absent from the contract")

    generated_schema = _without_null(generated_schema)
    shared_schema = _without_null(shared_schema)
    generated_ref = _schema_ref_name(generated_schema)
    shared_ref = _schema_ref_name(shared_schema)
    if generated_ref and shared_ref:
        if generated_ref != shared_ref:
            errors.append(f"{location}: expected schema {shared_ref}, generated {generated_ref}")
            return errors
        pair = (generated_ref, shared_ref)
        if pair in visited:
            return errors
        visited.add(pair)
        return errors + _compare_schema_recursive(
            generated_spec,
            shared_spec,
            _component(generated_spec, generated_ref),
            _component(shared_spec, shared_ref),
            f"{location} -> {shared_ref}",
            visited,
        )
    if generated_ref:
        marker = (generated_ref, f"inline:{location}")
        if marker in visited:
            return errors
        visited.add(marker)
        return errors + _compare_schema_recursive(
            generated_spec,
            shared_spec,
            _component(generated_spec, generated_ref),
            shared_schema,
            f"{location} -> {generated_ref}",
            visited,
        )
    if shared_ref:
        marker = (f"inline:{location}", shared_ref)
        if marker in visited:
            return errors
        visited.add(marker)
        return errors + _compare_schema_recursive(
            generated_spec,
            shared_spec,
            generated_schema,
            _component(shared_spec, shared_ref),
            f"{location} -> {shared_ref}",
            visited,
        )

    generated_type = generated_schema.get("type")
    shared_type = shared_schema.get("type")
    if generated_type is not None and shared_type is not None and generated_type != shared_type:
        errors.append(f"{location}: expected type {shared_type}, generated {generated_type}")

    # Some hand-authored 3.1 schemas redundantly include null in enum as well
    # as the type union; nullability is compared separately above.
    generated_enum = set(generated_schema.get("enum", [])) - {None}
    shared_enum = set(shared_schema.get("enum", [])) - {None}
    if generated_enum != shared_enum:
        errors.append(
            f"{location}: expected enum {sorted(shared_enum, key=repr)!r}, "
            f"generated {sorted(generated_enum, key=repr)!r}"
        )

    generated_required = set(generated_schema.get("required", []))
    shared_required = set(shared_schema.get("required", []))
    missing_required = shared_required - generated_required
    if missing_required:
        errors.append(
            f"{location}: generated schema is missing required {sorted(missing_required)!r}"
        )
    extra_required = generated_required - shared_required
    if extra_required and current_component in STRICT_REQUIRED_COMPONENTS:
        errors.append(
            f"{location}: generated schema adds required "
            f"{sorted(extra_required)!r} absent from the contract"
        )

    if current_component in STRICT_REQUIRED_COMPONENTS:
        for keyword in ("format", "minimum", "maximum"):
            if keyword in shared_schema and generated_schema.get(keyword) != shared_schema[keyword]:
                errors.append(
                    f"{location}: expected {keyword} {shared_schema[keyword]!r}, "
                    f"generated {generated_schema.get(keyword)!r}"
                )

    generated_properties = generated_schema.get("properties", {})
    shared_properties = shared_schema.get("properties", {})
    missing_properties = set(shared_properties) - set(generated_properties)
    if missing_properties:
        errors.append(
            f"{location}: generated schema is missing properties {sorted(missing_properties)!r}"
        )
    for field in sorted(set(generated_properties) & set(shared_properties)):
        errors.extend(
            _compare_schema_recursive(
                generated_spec,
                shared_spec,
                generated_properties[field],
                shared_properties[field],
                f"{location}.{field}",
                visited,
            )
        )

    if "items" in generated_schema or "items" in shared_schema:
        errors.extend(
            _compare_schema_recursive(
                generated_spec,
                shared_spec,
                generated_schema.get("items", {}),
                shared_schema.get("items", {}),
                f"{location}[]",
                visited,
            )
        )

    for keyword in ("anyOf", "oneOf", "allOf"):
        generated_options = generated_schema.get(keyword, [])
        shared_options = shared_schema.get(keyword, [])
        if len(generated_options) != len(shared_options):
            errors.append(
                f"{location}: {keyword} option count differs "
                f"({len(shared_options)} expected, {len(generated_options)} generated)"
            )
            continue
        for index, (generated_option, shared_option) in enumerate(
            zip(generated_options, shared_options, strict=True)
        ):
            errors.extend(
                _compare_schema_recursive(
                    generated_spec,
                    shared_spec,
                    generated_option,
                    shared_option,
                    f"{location}.{keyword}[{index}]",
                    visited,
                )
            )
    return errors


def _compare_response_schema(
    generated_spec: dict[str, Any],
    shared_spec: dict[str, Any],
    generated_schema: dict[str, Any],
    shared_schema: dict[str, Any],
    location: str,
) -> list[str]:
    return _compare_schema_recursive(
        generated_spec,
        shared_spec,
        generated_schema,
        shared_schema,
        location,
        set(),
    )


def check_implemented_routes(
    generated_spec: dict[str, Any] | None = None,
    shared_spec: dict[str, Any] | None = None,
) -> list[str]:
    generated_spec = generated_spec or build_openapi()
    shared_spec = shared_spec or load_shared_openapi()
    prefix = _server_prefix(shared_spec)
    errors: list[str] = []

    shared_paths = shared_spec.get("paths", {})

    for generated_path, generated_path_item in generated_spec.get("paths", {}).items():
        normalized_path = _normalize_generated_path(generated_path, prefix)
        shared_path_item = shared_paths.get(normalized_path)

        if shared_path_item is None:
            errors.append(f"{generated_path}: missing from shared OpenAPI contract")
            continue

        for method, generated_operation in _operation_items(generated_path_item):
            shared_operation = shared_path_item.get(method)
            location = f"{method.upper()} {normalized_path}"

            if shared_operation is None:
                errors.append(f"{location}: missing from shared OpenAPI contract")
                continue

            generated_operation_id = generated_operation.get("operationId")
            shared_operation_id = shared_operation.get("operationId")
            if generated_operation_id != shared_operation_id:
                errors.append(
                    f"{location}: expected operationId {shared_operation_id}, "
                    f"generated {generated_operation_id}"
                )

            shared_body = shared_operation.get("requestBody")
            generated_body = generated_operation.get("requestBody")
            if location not in STRICT_REQUEST_BODY_OPERATIONS:
                shared_body = generated_body = None
            if shared_body is not None and generated_body is None:
                errors.append(f"{location}: generated route is missing request body")
            elif shared_body is not None and generated_body is not None:
                if bool(shared_body.get("required")) != bool(generated_body.get("required")):
                    errors.append(f"{location}: request-body requiredness differs")
                shared_body_schema = (
                    shared_body.get("content", {}).get("application/json", {}).get("schema")
                )
                generated_body_schema = (
                    generated_body.get("content", {}).get("application/json", {}).get("schema")
                )
                if shared_body_schema and generated_body_schema:
                    errors.extend(
                        _compare_response_schema(
                            generated_spec,
                            shared_spec,
                            generated_body_schema,
                            shared_body_schema,
                            f"{location} request body",
                        )
                    )

            for status_code, shared_response in shared_operation.get("responses", {}).items():
                generated_response = generated_operation.get("responses", {}).get(status_code)
                if generated_response is None:
                    errors.append(f"{location}: missing response {status_code}")
                    continue

                shared_schema = (
                    shared_response.get("content", {}).get("application/json", {}).get("schema")
                )
                generated_schema = (
                    generated_response.get("content", {}).get("application/json", {}).get("schema")
                )

                if shared_schema and generated_schema:
                    errors.extend(
                        _compare_response_schema(
                            generated_spec,
                            shared_spec,
                            generated_schema,
                            shared_schema,
                            f"{location} {status_code}",
                        )
                    )

    return errors


def generate_openapi(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the FastAPI OpenAPI document.")
    parser.add_argument("--output", type=Path, help="Write JSON output to a file.")
    args = parser.parse_args(argv)

    payload = json.dumps(build_openapi(), indent=2, sort_keys=True) + "\n"

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)

    return 0


def check_openapi(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check implemented routes against shared OpenAPI.")
    parser.add_argument(
        "--contract",
        type=Path,
        default=SHARED_OPENAPI,
        help="Path to the shared OpenAPI YAML contract.",
    )
    args = parser.parse_args(argv)

    errors = check_implemented_routes(shared_spec=load_shared_openapi(args.contract))
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print("Implemented API routes match shared OpenAPI contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(check_openapi())
