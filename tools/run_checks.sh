#!/bin/sh
# Everything CI runs, offline. Use inside the test container:
#   docker build -t deapi-skills-test -f tests/Dockerfile tests
#   docker run --rm --network none -v "$PWD:/repo" -w /repo deapi-skills-test
set -u
status=0
run() {
  echo "== $*"
  "$@" || status=1
}
if python -c "import pytest" 2>/dev/null; then
  run python -m pytest -q tests/unit
else
  run python -m unittest discover -s tests/unit -t tests/unit
fi
# Examples of the deapi-api skill (Node, curl and the openai packages are optional:
# tests that need a missing tool are skipped; CI installs them).
run python -m unittest tests/test_deapi_api.py
run python tools/check_openapi.py
run python tools/check_mcp_names.py
run python tools/scan_leaks.py
run python tools/check_frontmatter.py
exit $status
