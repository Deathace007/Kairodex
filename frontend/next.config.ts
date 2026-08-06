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
};

export default nextConfig;
