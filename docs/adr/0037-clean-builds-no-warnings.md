# ADR 0037: Builds are warning-free — CI fails on any warning (iOS + backend)

## Status

Accepted.

## Context

The iOS build log had scrolled into a wall of warnings: 6 `@retroactive`
attributes that don't apply (the conformed types are in-module), a Swift 6
actor-isolation warning, and ~174 "nullable property not supported in OpenAPI
3.1" notices from the Swift client generator reading 3.0-style `nullable: true`
in the shared contract. Warnings that are tolerated accumulate until a real one
hides in the noise and nobody reads them.

## Decision

**A warning is a build failure. CI fails the iOS build and the Python backend
tests on any warning, so the count stays at zero rather than "a few we ignore".**

- **iOS:** `xcodebuild test` runs with `SWIFT_TREAT_WARNINGS_AS_ERRORS=YES`.
- **Backend:** pytest runs with `filterwarnings = ["error"]` (a deprecation,
  an unawaited coroutine, etc. fails the run); `ruff check` continues to gate
  lint.
- **The OpenAPI contract stays valid 3.1:** every nullable is expressed as the
  3.1 union `type: [X, "null"]` for plain fields. For a nullable field that
  references a shared schema, we simply DON'T mark it `nullable` and leave it
  optional (absent from `required`) — Swift's `decodeIfPresent` and the web
  client both decode an incoming JSON `null` to nil/undefined for an optional
  property, so the generated output is unchanged and the generator emits no
  warning. `nullable: true` no longer appears anywhere in the contract.

## Invariant

> `grep -r 'nullable: true' shared/openapi` returns nothing, the iOS build
> produces zero Swift warnings, and the backend test run produces zero Python
> warnings. New warnings fail CI; they are fixed, not suppressed. A genuinely
> unavoidable third-party warning may be ignored only by an explicit, narrowly
> scoped `filterwarnings`/pragma entry with a comment saying why.

## Rejected

- **Leave warnings as informational.** They pile up and hide the one that
  matters — exactly what happened here.
- **Convert nullable `$ref` fields to `anyOf: [$ref, {type: "null"}]`.** Apple's
  swift-openapi-generator mishandles that form — it drops fields or flips
  optional→required. Dropping the `nullable` marker on an already-optional field
  achieves the same runtime behavior with no breakage.
- **A repo-wide `-warnings-as-errors` on every SPM/tool build.** The generator
  tool's own build is third-party; we gate the app compile and the backend
  tests we own, not other people's tools.
