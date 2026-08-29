"""HTTP client for the raw ENTSO-E Transparency Platform REST API.

This module does HTTP and nothing else: every method returns the response
body as a list of unparsed XML documents. A list, because the platform zips
the response when it spans multiple documents — balancing data (A85/A86)
regularly arrives as `PK…` zip bytes whose members are one XML document each,
while a small window comes back as a single plain document. Parsing lives in
`xml_parsers.py`, normalization in `normalizers.py`. Endpoint parameters come
from docs/entsoe.md §3/§5/§7.

Deliberately synchronous (`requests`): fetches already run on a worker thread
via `asyncio.to_thread` in the provider, matching the pattern the entsoe-py
client used.
"""

import io
import logging
import re
import zipfile
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://web-api.tp.entsoe.eu/api"

# requests reads timeout=None as "wait forever"; a stalled connection would
# then hang the fetch permanently. With a timeout it becomes an ordinary error
# that the scheduler records and retries on the next interval.
REQUEST_TIMEOUT_SECONDS = 30


def _format_period(dt: datetime) -> str:
    """Format a datetime as the API's yyyyMMddHHmm, coerced to UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M")


def _extract_reason(body: str) -> str | None:
    """Pull the human-readable <Reason><text> out of an error response.

    A 400 body is an Acknowledgement_MarketDocument whose Reason/text says
    what was actually wrong ("Unknown area", "delivered time interval is not
    valid", …). A regex is enough here — this only decorates an exception
    message, so a malformed body must never raise a second error.
    """
    match = re.search(r"<text>(.*?)</text>", body, re.DOTALL)
    return match.group(1).strip() if match else None


class ENTSOERawClient:
    """Raw REST client. One method per series; each returns XML documents."""

    def __init__(self, api_key: str, timeout: int = REQUEST_TIMEOUT_SECONDS) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def _get(self, params: dict[str, str]) -> list[str]:
        # The token travels ONLY in this header. Putting it in `params` would
        # embed it in the URL, which shows up in DEBUG logs and tracebacks —
        # see docs/entsoe.md §6.
        response = requests.get(
            BASE_URL,
            params=params,
            headers={"SECURITY_TOKEN": self._api_key},
            timeout=self._timeout,
        )

        if response.status_code == 400:
            reason = _extract_reason(response.text)
            if reason:
                raise requests.HTTPError(
                    f"400 Bad Request from ENTSO-E: {reason}", response=response
                )
        response.raise_for_status()

        # Multi-document responses arrive zipped. The magic bytes are more
        # reliable than the Content-Type header here.
        if response.content[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                return [
                    archive.read(name).decode("utf-8")
                    for name in sorted(archive.namelist())
                ]
        return [response.text]

    def fetch_day_ahead_prices_xml(self, eic: str, start: datetime, end: datetime) -> list[str]:
        """Day-ahead prices (A44). Both domains must be the same bidding zone."""
        return self._get({
            "documentType": "A44",
            "in_Domain": eic,
            "out_Domain": eic,
            "periodStart": _format_period(start),
            "periodEnd": _format_period(end),
        })

    def fetch_load_xml(self, eic: str, start: datetime, end: datetime) -> list[str]:
        """Actual total load (A65, processType A16 = realised)."""
        return self._get({
            "documentType": "A65",
            "processType": "A16",
            "outBiddingZone_Domain": eic,
            "periodStart": _format_period(start),
            "periodEnd": _format_period(end),
        })

    def fetch_generation_xml(self, eic: str, start: datetime, end: datetime) -> list[str]:
        """Actual generation per production type (A75). psrType omitted = all types."""
        return self._get({
            "documentType": "A75",
            "processType": "A16",
            "in_Domain": eic,
            "periodStart": _format_period(start),
            "periodEnd": _format_period(end),
        })

    def fetch_imbalance_volumes_xml(self, eic: str, start: datetime, end: datetime) -> list[str]:
        """Total imbalance volumes (A86). businessType defaults to A19 server-side."""
        return self._get({
            "documentType": "A86",
            "controlArea_Domain": eic,
            "periodStart": _format_period(start),
            "periodEnd": _format_period(end),
        })

    def fetch_imbalance_prices_xml(self, eic: str, start: datetime, end: datetime) -> list[str]:
        """Imbalance prices (A85)."""
        return self._get({
            "documentType": "A85",
            "controlArea_Domain": eic,
            "periodStart": _format_period(start),
            "periodEnd": _format_period(end),
        })
