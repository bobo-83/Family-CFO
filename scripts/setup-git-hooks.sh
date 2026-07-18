#!/bin/sh
set -eu

git config --local commit.template .gitmessage
git config --local core.hooksPath .githooks
chmod +x .githooks/commit-msg .githooks/pre-commit \
  scripts/validate-commit-message.sh scripts/check-repo-hygiene.sh

printf '%s\n' "Configured local Git commit template and hooks."
