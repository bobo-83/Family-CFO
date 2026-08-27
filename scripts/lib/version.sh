#!/usr/bin/env bash
#
# The monorepo's version scheme (ADR 0074, amending ADR 0029). Sourced by
# scripts/patch.sh, scripts/doctor.sh and the three iOS deploy scripts.
#
# /VERSION holds a CONTRACT — MAJOR.MINOR — shared by every component. Each
# component carries its own BUILD integer beside its source:
#
#   /VERSION           0.157      the contract
#   apps/api/BUILD     4          -> the api reports 0.157.4
#   apps/web/BUILD     2          -> the dashboard reports 0.157.2
#   apps/ios/BUILD     1          -> the phone reports 0.157.1
#
# Two deployables are COMPATIBLE when their contracts match, whatever their
# builds are. That is the whole point: before this, every seam compared full
# version strings for equality, so a backend-only patch made a byte-identical
# app look stale and three separate places warned about nothing.
#
# The contract moves only for a change that breaks an existing client — an
# endpoint or field removed or renamed, a type changed, a newly required
# request field, or a migration an old client cannot tolerate. Adding an
# endpoint does not move it. When it does move, every BUILD resets to 0.
#
# This file exists because the composition rule was otherwise going to live in
# seven copies of the same `tr -d '[:space:]' < VERSION` one-liner.

# contract_version <repo-root> -> "0.157"
# Empty (and non-fatal) when /VERSION is missing, so callers can decide whether
# that is a skip or a failure — doctor.sh skips, patch.sh dies.
contract_version() { # contract_version <repo-root>
  local file="$1/VERSION"
  [ -f "$file" ] || return 0
  tr -d '[:space:]' < "$file"
}

# component_build <repo-root> <api|web|ios> -> "4"
component_build() { # component_build <repo-root> <component>
  local file="$1/apps/$2/BUILD"
  [ -f "$file" ] || return 0
  tr -d '[:space:]' < "$file"
}

# component_version <repo-root> <api|web|ios> -> "0.157.4"
# Returns non-zero if either half is missing, rather than composing something
# like "0.157." that would then be compared against and silently never match.
component_version() { # component_version <repo-root> <component>
  local contract build
  contract="$(contract_version "$1")"
  build="$(component_build "$1" "$2")"
  [ -n "$contract" ] && [ -n "$build" ] || return 1
  printf '%s.%s' "$contract" "$build"
}

# contract_of <version> -> the MAJOR.MINOR prefix of a full version string.
# "0.157.4" -> "0.157". A bare "0.157" is returned unchanged, so this is safe
# to apply to either form.
contract_of() { # contract_of <version>
  printf '%s' "$1" | cut -d. -f1,2
}

# versions_compatible <a> <b> — true when both name the same contract.
# Used instead of string equality everywhere two deployables are compared.
versions_compatible() { # versions_compatible <version-a> <version-b>
  [ -n "$1" ] && [ -n "$2" ] && [ "$(contract_of "$1")" = "$(contract_of "$2")" ]
}
