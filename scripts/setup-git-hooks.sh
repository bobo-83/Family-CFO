#!/bin/sh
set -eu

git config --local commit.template .gitmessage
git config --local core.hooksPath .githooks
chmod +x .githooks/commit-msg scripts/validate-commit-message.sh

printf '%s\n' "Configured local Git commit template and hooks."
