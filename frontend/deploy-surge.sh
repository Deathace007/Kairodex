#!/usr/bin/env bash
# Static-export build + Surge deploy — the periodic-snapshot mirror of
# the live dashboard, at app.swingpro.tech. Run from the VM (needs
# 127.0.0.1:8000 — the real kairodex-api — reachable during the build;
# see next.config.ts's own top comment for why this is a *separate*
# build mode from the live `kairodex-frontend` systemd service, not a
# replacement for it, and docs/PROGRESS.md §12f for the full account of
# what is and isn't safe/live about this mirror).
set -euo pipefail
cd "$(dirname "$0")"

NEXT_OUTPUT_MODE=export npm run build
surge ./out app.swingpro.tech
