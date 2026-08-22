#!/bin/sh
# Tests for .githooks/pre-push's deny-list scan (#106, ADR 0030).
#
# The bug this pins: the hook scanned a commit's WHOLE diff, so a commit whose
# only purpose was removing a denied identifier carried it on its removed lines
# and was blocked. The guard prevented its own remedy.
#
# Everything here uses an invented token. This file must never contain a real
# denied value — the same rule the hooks themselves follow.
set -eu

TOKEN="Zzyzx-Invented-Denylist-Token"
REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0
ok() { printf '  PASS  %s\n' "$1"; pass=$((pass + 1)); }
ko() { printf '  FAIL  %s\n' "$1"; fail=$((fail + 1)); }

git init --quiet --bare "$WORK/remote.git"
git init --quiet "$WORK/work"
cd "$WORK/work"
git config user.email "test@example.invalid"
git config user.name "Test"
git config commit.gpgsign false
git remote add origin "$WORK/remote.git"

# The hook shells out to these by repo-relative path; both now source the
# shape expansion (#118), so the lib has to come along or the guard silently
# does nothing -- which is how this test caught a real regression.
mkdir -p scripts/lib .githooks
cp "$REPO_ROOT/scripts/check-repo-hygiene.sh" scripts/
cp "$REPO_ROOT/scripts/lib/deny-terms.sh" scripts/lib/
cp "$REPO_ROOT/.githooks/pre-push" .githooks/
chmod +x scripts/check-repo-hygiene.sh scripts/lib/deny-terms.sh .githooks/pre-push

# The deny list is gitignored in the real repo, and must be here too: `git add
# -A` would otherwise track the one file that legitimately contains every
# denied literal, and the tree scan would fail on the guard's own ammunition.
printf '.repo-hygiene-deny\n' > .gitignore

# Seed a baseline WITHOUT the hook, so the "remove it again" case has something
# to remove on the remote.
printf 'clean\n' > fixture.txt
git add -A
git commit --quiet -m "seed"
git push --quiet origin HEAD:refs/heads/main 2>/dev/null
git branch --quiet -M main
git branch --quiet --set-upstream-to=origin/main main 2>/dev/null || true

# Seed a commit that CONTAINS the token, still without the hook installed.
printf 'displayName: "%s"\n' "$TOKEN" > fixture.txt
git add -A
git commit --quiet -m "seed the identifier"
git push --quiet origin main 2>/dev/null

# From here on the hook is live, and the deny list exists.
printf '%s\n' "$TOKEN" > .repo-hygiene-deny
git config core.hooksPath .githooks

# --- the regression: removing a denied identifier must be pushable -----------
printf 'displayName: "demo-household"\n' > fixture.txt
git add -A
git commit --quiet -m "remove the identifier"
if git push --quiet origin main 2>/dev/null; then
  ok "a commit that only REMOVES a denied identifier can be pushed"
else
  ko "a commit that only REMOVES a denied identifier was blocked (#106)"
fi

# --- the case the hook exists for: adding one must still be blocked ----------
printf 'displayName: "%s"\n' "$TOKEN" > fixture.txt
git add -A
git commit --quiet -m "add it back"
if git push --quiet origin main 2>/dev/null; then
  ko "a commit that ADDS a denied identifier was allowed through"
else
  ok "a commit that ADDS a denied identifier is blocked"
fi
git reset --quiet --hard HEAD~1

# --- moving one is adding one, at the destination ----------------------------
printf 'clean\n' > fixture.txt
printf 'displayName: "%s"\n' "$TOKEN" > moved.txt
git add -A
git commit --quiet -m "move it elsewhere"
if git push --quiet origin main 2>/dev/null; then
  ko "a commit that MOVES a denied identifier was allowed through"
else
  ok "a commit that MOVES a denied identifier is blocked at its destination"
fi
git reset --quiet --hard HEAD~1
rm -f moved.txt

# --- the message has no notion of added vs removed, so it is scanned whole ---
printf 'still clean\n' > fixture.txt
git add -A
git commit --quiet -m "mentions $TOKEN in the message"
if git push --quiet origin main 2>/dev/null; then
  ko "a denied identifier in the commit MESSAGE was allowed through"
else
  ok "a denied identifier in the commit MESSAGE is blocked"
fi
git reset --quiet --hard HEAD~1

# --- an identifier left in the tree is the tree scan's job, not the diff's ---
printf 'displayName: "%s"\n' "$TOKEN" > lingering.txt
git add -A
git commit --quiet -m "leave one lying around"
git reset --quiet --hard HEAD~1
printf 'displayName: "%s"\n' "$TOKEN" > lingering.txt  # untracked-but-present
git add lingering.txt
if scripts/check-repo-hygiene.sh >/dev/null 2>&1; then
  ko "an identifier present in the tree passed the tree scan"
else
  ok "an identifier present in the tree is caught by the tree scan"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
