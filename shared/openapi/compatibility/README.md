# Client compatibility fixtures

Each `<MAJOR>.<MINOR>.yaml` file is the oldest API contract clients carrying
that product contract must support (ADR 0074). The fixture is copied from the
OpenAPI document when a contract is established and is immutable after merge.

`scripts/check-client-compatibility.sh` regenerates the real web or iOS client
from this fixture and compiles the application. This catches a client beginning
to use an operation, field, enum value, or type that an older API on the same
contract does not provide.

When `/VERSION` changes, add a new fixture for the new value. Never edit or
delete a fixture already present on the base branch; CI rejects that history
rewrite.
