/** Shared by the two dynamic-segment routes' conditional
 * `generateStaticParams` — see next.config.ts's own top comment for the
 * two build modes this switches between.
 *
 * Route-level rendering mode itself (`export const dynamic = ...`) is
 * NOT set from here — Next.js requires that specific export to be a
 * literal string its build-time analyzer can read statically, so it
 * can't be computed from an imported constant. Each build mode gets the
 * right behavior a different way instead: the live server build's pages
 * fetch with `cache: "no-store"` (lib/api.ts), which itself makes
 * Next.js render dynamically per request — no separate `dynamic` export
 * needed; the Surge static export fetches with the ordinary default
 * cache, at build time only.
 */
export const IS_STATIC_EXPORT = process.env.NEXT_OUTPUT_MODE === "export";
