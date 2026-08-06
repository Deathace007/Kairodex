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

# Drop the Data Cache before every snapshot. The export build fetches at
# build time with Next's *default* caching (a static export has no server,
# so `no-store` isn't available to it — see lib/api.ts), and Next persists
# those fetch results under `<distDir>/cache/fetch-cache` ACROSS builds.
# The 5-minute timer was therefore rebuilding faithfully and re-baking the
# same cached API responses each time, so the "periodic snapshot" stopped
# tracking reality the moment the cache was first written — live 2026-08-06
# it was still serving a positions payload from before profit_target
# existed, rendering Target as "—" against an API that had it.
# Cheap to discard: this is one small localhost fetch per panel.
#
# The path is under `.next`, NOT under the export's own distDir: Next keeps
# the Data Cache in `.next/cache` even when `distDir` moves the export
# output elsewhere. Clearing only the fetch cache leaves the live server's
# build (`.next/BUILD_ID`, `.next/server/...`) untouched.
rm -rf .next/cache/fetch-cache

NEXT_OUTPUT_MODE=export npm run build

# Publish the export's distDir, which with `output: "export"` IS the built
# site (see next.config.ts). Publishing `./out` here silently shipped a
# stale directory left over from before distDir was split — Surge reported
# "Success!" either way, since it only ever checked that the path existed.
surge ./.next-export app.swingpro.tech
