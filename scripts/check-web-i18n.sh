#!/usr/bin/env bash
#
# One definition of "the translations are OK" (#33), used by CI and by hand.
#
# The failure this exists to prevent: a check shaped like
#
#     MISSING=$(npm run build 2>&1 | grep -c "No translation found")
#
# reports SUCCESS when the build dies before printing anything — a corrupt
# XLIFF emits no warnings, so `grep -c` returns 0 and the catalogs look fine
# while the app cannot be built at all. That happened, and the broken files
# were reported as verified.
#
# So the order below matters: structure first (cheap, and the specific thing
# that broke), then the build's EXIT CODE, and only then the warning text.
#
# Usage: scripts/check-web-i18n.sh [--skip-build]
#   --skip-build  structure checks only (fast; no node required)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$REPO_ROOT/apps/web"
LOCALE_DIR="$WEB_DIR/src/locale"

fail() { printf '\033[1;31mi18n-check:\033[0m %s\n' "$1" >&2; exit 1; }
note() { printf '\033[1;36mi18n-check:\033[0m %s\n' "$1"; }

# --- 1. Structure -----------------------------------------------------------
# A catalog that doesn't parse takes the whole build down, and the build's
# error names a line number rather than the problem. Say it plainly here.
note "checking catalog structure"
python3 - "$LOCALE_DIR" <<'PY'
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

locale_dir = sys.argv[1]
paths = sorted(glob.glob(os.path.join(locale_dir, "messages*.xlf")))
if not paths:
    print("no message catalogs found", file=sys.stderr)
    sys.exit(1)

problems = []
for path in paths:
    name = os.path.basename(path)
    text = open(path, encoding="utf-8").read()
    try:
        ET.parse(path)
    except ET.ParseError as exc:
        problems.append(f"{name}: not well-formed XML — {exc}")
        # Locate the unit that never closed: the parser's line number points at
        # the symptom (usually </body>), not the cause.
        depth, start = 0, None
        for i, line in enumerate(text.split("\n"), 1):
            if "<trans-unit " in line:
                if depth == 1:
                    problems.append(
                        f"{name}: <trans-unit> opened at line {start} is never closed "
                        f"(next unit starts at line {i})"
                    )
                    break
                depth, start = 1, i
            if "</trans-unit>" in line:
                depth = 0
        continue

    opened, closed = text.count("<trans-unit "), text.count("</trans-unit>")
    if opened != closed:
        problems.append(f"{name}: {opened} <trans-unit> vs {closed} </trans-unit>")

    ids = re.findall(r'<trans-unit id="([^"]+)"', text)
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        problems.append(f"{name}: duplicate ids {sorted(duplicates)[:5]}")

    print(f"  {name}: {len(ids)} units, well-formed")

if problems:
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    sys.exit(1)
PY

if [ "${1:-}" = "--skip-build" ]; then
  note "OK (structure only)"
  exit 0
fi

# --- 2. The build must SUCCEED ---------------------------------------------
# Checked by exit code, before any output is inspected. This is the step whose
# absence caused the false pass.
note "building all locales"
cd "$WEB_DIR"
set +e
npm run build >/tmp/web-i18n-build.log 2>&1
build_status=$?
set -e
if [ "$build_status" -ne 0 ]; then
  tail -30 /tmp/web-i18n-build.log >&2
  fail "the production build FAILED (exit $build_status) — see the log above"
fi

# --- 3. Only now, the warnings ---------------------------------------------
# Compile-time i18n's whole point: an extracted string left untranslated in any
# locale must not merge.
if grep -q "No translation found" /tmp/web-i18n-build.log; then
  printf '\033[1;31mUntranslated messages\033[0m — add them to src/locale/messages.<locale>.xlf:\n' >&2
  grep "No translation found" /tmp/web-i18n-build.log | sed 's/^/  /' | head -40 >&2
  fail "$(grep -c 'No translation found' /tmp/web-i18n-build.log) untranslated message(s)"
fi

note "OK — build succeeded, every locale fully translated"
