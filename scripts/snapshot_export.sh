#!/usr/bin/env bash
# One-time snapshot of this install's state (database + uploaded/generated files)
# for copying to another machine. Run from the repo root while the stack is up:
#   bash scripts/snapshot_export.sh
# Output: snapshot/db.dump and snapshot/data.tgz (copy the whole folder).
# The search index itself stays in OpenAI's vector store; the other install
# must use an API key from the same OpenAI project to search it.
set -euo pipefail
cd "$(dirname "$0")/.."

out=snapshot
mkdir -p "$out"

echo "→ dumping database"
docker compose exec -T postgres pg_dump -U nhi_ai -d nhi_ai -Fc --no-owner -f /tmp/db.dump
docker compose cp postgres:/tmp/db.dump "$out/db.dump"
docker compose exec -T postgres rm -f /tmp/db.dump

echo "→ archiving /data (documents, generated files, job outputs)"
docker compose exec -T backend tar czf /tmp/data.tgz -C /data .
docker compose cp backend:/tmp/data.tgz "$out/data.tgz"
docker compose exec -T backend rm -f /tmp/data.tgz

git rev-parse HEAD > "$out/commit.txt"
echo "✓ snapshot written to $out/ ($(du -sh "$out" | cut -f1)); code version $(cut -c1-8 "$out/commit.txt")"
