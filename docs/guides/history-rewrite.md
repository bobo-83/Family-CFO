# Rewriting git history to remove denied identifiers (#117)

This is the runbook for scrubbing deny-listed values from git **history**
(the tree is guarded separately — see ADR 0030). It exists because this has
now been needed twice, and the second time proved the first pass incomplete:
a hand-enumerated replacement set misses forms nobody thought to write down.

The replacement rules are therefore **derived, not written**:
`scripts/generate-history-replacements.sh` expands the gitignored
`.repo-hygiene-deny` through the same library the guards use, so the rewrite
erases exactly what the guards detect. Detector and eraser cannot disagree.

## Before deciding, know the cost

- every existing clone is invalidated: re-clone, never push from an old one
- open PRs must be rebased onto the rewritten base
- commit SHAs change, so SHA references in issues/ADRs/logs go stale
- CI reruns everything

## Order matters: delete stale branches FIRST

A rewrite only covers refs that exist. Stale branches do double damage:
their **tips** may still expose the identifier in a browsable tree (52 of 59
did, when #117 was measured), and a branch forked before an *earlier* rewrite
keeps the entire pre-rewrite history alive on the remote — which is how the
August 2026 rewrite was quietly undone by one forgotten July branch.

So: delete every branch that is merged or abandoned, then rewrite what
remains. Fewer refs, smaller blast radius, and the deletions alone remove
most of the live exposure without any rewrite at all.

## Procedure

1. **Freeze**: no pushes from any machine until done. Note open PRs.
2. **Prune branches**: delete merged/abandoned remote branches
   (`git push origin -d <branch>`). Verify nothing unique is lost first:
   `git log origin/main..origin/<branch>` should show only commits that are
   merged, superseded, or disposable.
3. **Fresh mirror**: `git clone --mirror git@github.com:OWNER/REPO.git work.git`
4. **Generate rules** (output holds cleartext values — mktemp, never a
   tracked path):

       RULES=$(mktemp)
       trap 'rm -f "$RULES"' EXIT
       scripts/generate-history-replacements.sh /path/to/.repo-hygiene-deny > "$RULES"

5. **Rewrite**: `cd work.git && git filter-repo --replace-text "$RULES"`
   (`pip3 install --user git-filter-repo` if missing).
6. **Verify before pushing** — scan the rewritten history with the real
   matchers and require zero:

       . scripts/lib/deny-terms.sh
       expand_deny_terms /path/to/.repo-hygiene-deny | while IFS="$DENY_TAB" read -r mode label pattern; do
         [ -z "$pattern" ] && continue
         case "$mode" in
           F) git log --all --format=%H -S"$pattern" ;;
           E) git log --all --format=%H -G"$pattern" ;;
         esac
       done | sort -u
       # any output at all = do not push

   Also confirm the tip tree of `main` is unchanged (`git rev-parse
   'main^{tree}'` before vs after) — a correct rewrite touches history only,
   because the tip was already cleaned by ordinary commits.
7. **Push**: `git push --force --all && git push --force --tags`, then
   re-clone every working machine and rebase open PRs.
8. **Afterwards**: GitHub may cache old commits reachable by SHA for a
   while; contact GitHub support to clear cached views if the exposure
   warrants it.

## Dry run first, always

Steps 3–6 against a local `file://` clone instead of the GitHub remote cost
nothing and prove the rules on your actual history. Only the force-push is
irreversible.
