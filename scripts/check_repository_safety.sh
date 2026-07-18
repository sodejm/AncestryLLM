#!/usr/bin/env bash
set -Eeuo pipefail

blocked='\.(rmtree|rmgc|db|sqlite|sqlite3|ged|gedcom|log|sarif)$|(^|/)(\.env|family_trees/.+)$'
tracked="$(git ls-files | grep -E "$blocked" | grep -Ev '^(tests/fixtures/|\.env\.example$|family_trees/\.gitkeep$)' || true)"
if [[ -n "$tracked" ]]; then
  printf '%s\n' "$tracked"
  echo "repository safety check: private/runtime artifact is tracked" >&2
  exit 1
fi

if git grep -IEn 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY' -- ':!tests' ':!.env.example'; then
  echo "repository safety check: possible committed private key" >&2
  exit 1
fi

echo "repository safety check passed"
