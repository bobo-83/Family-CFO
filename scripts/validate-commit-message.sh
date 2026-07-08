#!/bin/sh
set -eu

message_file=${1:-}

if [ -z "$message_file" ]; then
  printf '%s\n' "Usage: scripts/validate-commit-message.sh <commit-message-file>" >&2
  exit 2
fi

content=$(sed '/^[[:space:]]*#/d; s/\r$//' "$message_file")
subject=$(printf '%s\n' "$content" | sed '/^[[:space:]]*$/d' | sed -n '1p')

fail() {
  printf '%s\n' "Commit message does not match .gitmessage: $1" >&2
  exit 1
}

[ -n "$subject" ] || fail "missing subject"

case "$subject" in
  feat\(*\):\ *|fix\(*\):\ *|docs\(*\):\ *|test\(*\):\ *|refactor\(*\):\ *|chore\(*\):\ *|build\(*\):\ *|ci\(*\):\ *) ;;
  *) fail "subject must be '<type>(<scope>): <short summary>'" ;;
esac

subject_length=${#subject}
[ "$subject_length" -le 72 ] || fail "subject must be 72 characters or fewer"

require_section() {
  section=$1
  printf '%s\n' "$content" | grep -qx "$section" || fail "missing '$section' section"
}

require_section "Why"
require_section "What changed"
require_section "Verification"
require_section "Sensitive data check"

require_bullet_after() {
  section=$1
  stop_pattern=$2
  body=$(printf '%s\n' "$content" | awk -v section="$section" -v stop="$stop_pattern" '
    $0 == section { in_section = 1; next }
    in_section && $0 ~ stop { exit }
    in_section { print }
  ')

  printf '%s\n' "$body" | grep -Eq '^[[:space:]]*-[[:space:]][^[:space:]]' ||
    fail "'$section' must include at least one bullet"
}

require_bullet_after "Why" "^(What changed|Verification|Sensitive data check)$"
require_bullet_after "What changed" "^(Verification|Sensitive data check)$"
require_bullet_after "Verification" "^Sensitive data check$"
require_bullet_after "Sensitive data check" "__NO_NEXT_SECTION__"
