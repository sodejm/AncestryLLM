#!/usr/bin/env bash
# Repository safety gate: fails if privacy-sensitive artifacts (genealogy
# databases, exports, secrets, private keys) are Git-tracked. Read-only; makes
# no network calls and requires no credentials. Exit 0 when clean, 1 otherwise.
set -Eeuo pipefail

blocked='\.(rmtree|rmgc|db|sqlite|sqlite3|ged|gedcom|log|sarif|p12|pfx|p8|pem|key|asc|gpg|b64|mobileprovision)$|(^|/)(\.env(\..+)?|family_trees/.+|secure/.+|secrets/.+)$'
allowed='^(tests/fixtures/.*\.(ged|gedcom)|\.env\.example|family_trees/\.gitkeep)$'
tracked="$(git ls-files | grep -E "$blocked" | grep -Ev "$allowed" || true)"
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
