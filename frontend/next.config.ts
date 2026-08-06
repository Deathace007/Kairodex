import type { NextConfig } from "next";

// Two build modes from one source tree, toggled by an env var rather
// than a second checkout:
//
// - Default (VM's own `kairodex-frontend` systemd unit, `next start`):
//   a real Node server, `force-dynamic` pages, every fetch fresh —
//   see docs/PROGRESS.md §12c for why this is the live, correct-by-
//   construction dashboard, reached over an SSH tunnel.
// - `NEXT_OUTPUT_MODE=export` (`frontend/deploy-surge.sh`): a static
//   export for Surge (`app.swingpro.tech`) — Surge only serves static
//   files, no Node process, so `force-dynamic` (which needs a server on
//   every request) is structurally impossible there. The build itself
//   still runs ON THE VM (where the API's own `127.0.0.1:8000` is real),
//   so the exported HTML is baked with genuinely real data as of build
//   time; a periodic systemd timer rebuilds+redeploys every few minutes
//   to keep it a fresh-enough snapshot. The API itself is never exposed
//   publicly this way — deliberately: it holds `POST /api/kill` and the
//   other audited control endpoints, and this repo's own security
//   posture (SPEC_REVIEW.md B5, `kairodex.api.main`'s docstring) is
//   "binds localhost, reached through a tunnel," not "public internet."
const isStaticExport = process.env.NEXT_OUTPUT_MODE === "export";

const nextConfig: NextConfig = {
  output: isStaticExport ? "export" : undefined,
  // The two modes MUST NOT share a build directory. Both used to write the
  // default `.next`, and since the export build runs on the same VM, in the
  // same checkout, as the live `kairodex-frontend` service (which is just
  // `next start` reading `.next`), every Surge deploy silently replaced the
  // live server's build with a static export — and the 5-minute timer did it
  // again every 5 minutes. The "live" dashboard was serving data frozen at
  // the last export build, which is exactly what this file's comment above
  // says the export must never become: a *separate* mode, "not a
  // replacement for it". Found 2026-08-06 by noticing the dashboard showed
  // a stop of 376.04 while the API simultaneously returned 384.65.
  //
  // NOTE, and it is not obvious: with `output: "export"`, `distDir` IS the
  // directory the exported *site* is written to — it does not merely move
  // build artifacts and leave `out/` as the export target. So
  // `.next-export/` is what deploy-surge.sh publishes, and no `out/` is
  // produced in this mode at all. An earlier revision of this very comment
  // asserted the opposite, and the deploy script duly kept publishing a
  // stale leftover `out/` while reporting "Success!". The export build does
  // still share `.next/cache` with the live build for Next's Data Cache,
  // which is why deploy-surge.sh clears the fetch cache there and not under
  // distDir.
  distDir: isStaticExport ? ".next-export" : undefined,
};

export default nextConfig;
