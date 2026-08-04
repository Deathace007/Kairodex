"""Error hierarchy. Callers catch by category (VendorError) or by exact
cause (RateLimitError) depending on how they need to react."""

from __future__ import annotations


class OtpError(Exception):
    """Base for every error raised by this codebase."""


class ConfigError(OtpError):
    """Bad or missing configuration — fail fast at startup, not mid-run."""


class VendorError(OtpError):
    """A market-data vendor (Upstox, LSE) misbehaved."""


class AuthError(VendorError):
    """Token missing, expired, or rejected. See kairodex.data.upstox.auth for the
    daily-reauth handling this exists to make loud (SPEC_REVIEW.md C1)."""


class RateLimitError(VendorError):
    """Vendor quota or rate limit hit. Callers should back off, not retry
    immediately — see kairodex.data.*.ratelimit."""


class DataQualityError(OtpError):
    """A record failed validation (crossed book, stale, malformed) and was
    quarantined rather than silently dropped (SPEC_REVIEW.md C5, C9)."""
