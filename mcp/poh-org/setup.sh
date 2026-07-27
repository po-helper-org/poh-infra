#!/usr/bin/env bash
# Reproducible build of the POH Org Docker MCP Toolkit profile.
# Idempotent-ish: re-running recreates images and re-adds servers.
# Secrets / OAuth / dokploy.url are NOT set here — see README "User steps".
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMG="$HERE/images"
ORG="$(cd "$HERE/../../.." && pwd)"          # /Users/<you>/projects/poh-org
CATALOG=poh-org
PROFILE=poh_

echo "== 1. Build wrapper images =="
docker build -t mcp/poh-repowise:latest -f "$IMG/Dockerfile.repowise" "$IMG"
docker build -t mcp/poh-backlog:latest  -f "$IMG/Dockerfile.backlog"  "$IMG"
docker build -t mcp/poh-dokploy:latest  -f "$IMG/Dockerfile.dokploy"  "$IMG"
# docker-control: build from upstream (not vendored in git)
[ -d "$IMG/mcp-server-docker" ] || git clone --depth 1 https://github.com/ckreiling/mcp-server-docker "$IMG/mcp-server-docker"
docker build -t mcp/poh-docker:latest "$IMG/mcp-server-docker"

echo "== 2. Custom catalog =="
docker mcp catalog create "$CATALOG" --title "POH Org" 2>/dev/null || echo "  catalog exists, reusing"
# official servers (referenced from the Docker MCP catalog)
OFF=mcp/docker-mcp-catalog:latest
for s in sentry-remote render github-official filesystem sequentialthinking; do
  docker mcp catalog server add "$CATALOG" --server "catalog://$OFF/$s" || true
done
# custom servers (local spec files with env / secrets / volumes)
for s in dokploy docker repowise backlog postgres; do
  docker mcp catalog server add "$CATALOG" --server "file://$HERE/servers/$s.yaml" || true
done

echo "== 3. Profile $PROFILE =="
if ! docker mcp profile ls | awk '{print $1}' | grep -qx "$PROFILE"; then
  printf 'version: 1\nid: %s\nname: '\''POH Org'\''\nservers: []\n' "$PROFILE" > /tmp/${PROFILE}_init.yaml
  docker mcp profile import /tmp/${PROFILE}_init.yaml
fi
for s in render sentry-remote github-official filesystem sequentialthinking dokploy docker repowise backlog postgres; do
  docker mcp profile server add "$PROFILE" --server "catalog://$CATALOG:latest/$s" || true
done

echo "== 4. Non-secret config (host-specific defaults; edit as needed) =="
docker mcp profile config "$PROFILE" --set "filesystem.paths=$ORG"
docker mcp profile config "$PROFILE" --set "repowise.mount=$ORG:/workspace"
docker mcp profile config "$PROFILE" --set "backlog.mount=$ORG:/workspace"
# dokploy.url is deployment-specific — set it yourself:
#   docker mcp profile config poh_ --set dokploy.url=https://your-dokploy-host

echo "== 5. Validate =="
docker mcp gateway run --profile "$PROFILE" --dry-run 2>&1 | grep -E "enabled:|tools\)|Can't start|tools listed" || true

echo
echo "DONE. Next: set secrets + OAuth + dokploy.url (README 'User steps'), then activate the profile for your client."
