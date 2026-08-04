"""Analytics Token handling.

Upstox's Analytics Token is a long-lived (1 year), read-only bearer token
generated manually from the Developer Apps dashboard — there is no OAuth
dance and no daily re-authentication for market data (confirmed against
current Upstox docs; supersedes the daily-expiry assumption originally
written into SPEC_REVIEW.md C1, corrected there — see that file's note).

It cannot place orders, which is a free structural reinforcement of the
paper-only guarantee on the NSE side: this credential physically can't
touch a live order even if something upstream misconfigured itself.

There is no endpoint to check the token's remaining validity, so expiry is
tracked from the date the user reads off the dashboard at generation time.
"""

from __future__ import annotations

import datetime

from kairodex.core.errors import AuthError


class AnalyticsToken:
    def __init__(self, token: str | None, expires_at: datetime.date | None) -> None:
        if not token:
            raise AuthError(
                "UPSTOX_ACCESS_TOKEN is not set. Generate one at "
                "Developer Apps -> Analytics tab -> Generate Token."
            )
        self._token = token
        self._expires_at = expires_at

    @property
    def bearer_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def days_until_expiry(self, today: datetime.date | None = None) -> int | None:
        if self._expires_at is None:
            return None
        return (self._expires_at - (today or datetime.date.today())).days

    def is_expiring_soon(self, threshold_days: int = 14) -> bool:
        days = self.days_until_expiry()
        return days is not None and days <= threshold_days
