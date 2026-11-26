#!/usr/bin/env bash
# Deploy to a Hugging Face Docker Space.
#   ./push.sh username/claims-console "Deploy claims console"
set -euo pipefail

SPACE="${1:?usage: push.sh <user>/<space> [message]}"
MESSAGE="${2:-Deploy claims console}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"

git clone "https://huggingface.co/spaces/${SPACE}" "$TMP"

for item in Dockerfile requirements.txt pyproject.toml src prompts eval models data; do
  if [ -e "$ROOT/$item" ]; then cp -R "$ROOT/$item" "$TMP/"; fi
done

mkdir -p "$TMP/frontend"
(cd "$ROOT/frontend" && tar --exclude=node_modules --exclude=dist -cf - .) | (cd "$TMP/frontend" && tar -xf -)

cp "$(dirname "${BASH_SOURCE[0]}")/README.md" "$TMP/README.md"

rm -f "$TMP/.env"
rm -rf "$TMP/data/uploads" "$TMP/data/runs.jsonl" "$TMP/data/llm_usage.jsonl" "$TMP/data/case_queue.json"

git -C "$TMP" add -A
git -C "$TMP" -c user.name=deploy -c user.email=deploy@local commit -m "$MESSAGE" || true
git -C "$TMP" push

echo "Pushed. Set the Space variables from DEPLOY.md."
