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
# A marked two-word entry, so the shape expansion is exercised through the real
# hooks and not only in its own unit tests. Nothing in this repo contains
# "Zyzzogeton", so a derived form matching is the expansion working, not luck.
NAME="Quorra Zyzzogeton"
NAME_DERIVED="The Zyzzogetons"
NAME_DERIVED_WORD="Zyzzogetons"  # for building a longer word around the form
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

# --- the expansion is actually wired into the hooks, not just unit-tested -----
# Without this, reverting the hooks to read the raw deny list instead of the
# expanded terms leaves every other test in this file green: TOKEN is a single
# word and never expands, so it cannot tell the two apart.
git reset --quiet --hard origin/main
printf '%s\nname: %s\n' "$TOKEN" "$NAME" > .repo-hygiene-deny

# The derived form goes in the MESSAGE and nowhere else, so the working tree
# stays clean. That isolates pre-push's own scan: check-repo-hygiene.sh runs
# first inside the hook, and if the form were in a file the tree scan would
# catch it and this would pass even with the diff scan unwired.
printf 'clean\n' > shapes.txt
git add -A
git commit --quiet -m "mentions $NAME_DERIVED in the message"
if git push --quiet origin main 2>/dev/null; then
  ko "a DERIVED name form in the MESSAGE was allowed through pre-push (#118)"
else
  ok "a DERIVED name form in the MESSAGE is blocked by pre-push"
fi
git reset --quiet --hard HEAD~1

# ...and the tree scan agrees, which is the point of the shared lib.
printf 'household: "%s"\n' "$NAME_DERIVED" > shapes.txt
git add shapes.txt
hygiene_out=$(scripts/check-repo-hygiene.sh 2>&1 || true)
if printf '%s\n' "$hygiene_out" | grep -q 'denylisted identifier'; then
  ok "a DERIVED name form is caught by the tree scan"
else
  ko "a DERIVED name form passed the tree scan (#118)"
fi
# A hit must say WHERE, or a false positive gets answered with --no-verify,
# and it must hint at the per-entry demotion escape hatch for the same reason.
if printf '%s\n' "$hygiene_out" | grep -q 'shapes.txt:1'; then
  ok "a tree-scan hit names its file and line"
else
  ko "a tree-scan hit does not say where it matched"
fi
if printf '%s\n' "$hygiene_out" | grep -q 'name:.*marker'; then
  ok "a derived-form hit explains the demotion escape hatch"
else
  ko "a derived-form hit gives no way out but --no-verify"
fi
rm -f shapes.txt
git reset --quiet --hard origin/main

# --- boundaries: a derived form inside a longer word is prose, not a leak ----
# This is the false positive class that turned `Tran` into `Transaction`:
# 14 of 56 plausible surnames collided with this repo as substrings, and every
# derived form contains a space, so bounding costs no detection.
printf 'notes on the %sky affair\n' "$NAME_DERIVED_WORD" > prose.txt
git add -A
git commit --quiet -m "prose that merely contains a derived form's letters"
if git push --quiet origin main 2>/dev/null; then
  ok "a derived form inside a longer word is not flagged (word boundaries)"
else
  ko "boundary matching regressed to substring"
fi

# An unmarked two-word entry must NOT expand: "Chase Bank" deriving "Banks"
# would match ordinary code and block every push.
printf '%s\n' "$NAME" > .repo-hygiene-deny
printf 'household: "%s"\n' "$NAME_DERIVED" > shapes.txt
git add -A
git commit --quiet -m "derived form, unmarked entry"
if git push --quiet origin main 2>/dev/null; then
  ok "an UNMARKED entry does not expand, so a derived form passes"
else
  ko "an UNMARKED entry expanded anyway"
fi
git reset --quiet --hard origin/main
rm -f shapes.txt

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
