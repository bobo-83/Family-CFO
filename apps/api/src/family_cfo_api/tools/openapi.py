from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import yaml

from family_cfo_api.main import create_app

HTTP_METHODS = {"delete", "get", "patch", "post", "put"}
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


def _compare_response_schema(
    generated_spec: dict[str, Any],
    shared_spec: dict[str, Any],
    generated_schema: dict[str, Any],
    shared_schema: dict[str, Any],
    location: str,
) -> list[str]:
    errors: list[str] = []
    generated_ref = _schema_ref_name(generated_schema)
    shared_ref = _schema_ref_name(shared_schema)

    if generated_ref != shared_ref:
        errors.append(f"{location}: expected schema {shared_ref}, generated {generated_ref}")
        return errors

    if not shared_ref:
        return errors

    generated_component = generated_spec["components"]["schemas"].get(generated_ref, {})
    shared_component = shared_spec["components"]["schemas"].get(shared_ref, {})

    for field in shared_component.get("required", []):
        if field not in generated_component.get("required", []):
            errors.append(f"{location}: generated schema is missing required field {field}")

    shared_properties = shared_component.get("properties", {})
    generated_properties = generated_component.get("properties", {})
    for field in shared_properties:
        if field not in generated_properties:
            errors.append(f"{location}: generated schema is missing property {field}")

    return errors


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

            for status_code, shared_response in shared_operation.get("responses", {}).items():
                generated_response = generated_operation.get("responses", {}).get(status_code)
                if generated_response is None:
                    errors.append(f"{location}: missing response {status_code}")
                    continue

                shared_schema = (
                    shared_response.get("content", {})
                    .get("application/json", {})
                    .get("schema")
                )
                generated_schema = (
                    generated_response.get("content", {})
                    .get("application/json", {})
                    .get("schema")
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

