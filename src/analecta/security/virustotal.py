"""VirusTotal URL scanner — M7 security module.

Public API rate limits: 4 requests/minute · 500 requests/day.
A minimum 15-second delay between consecutive API calls is enforced by
the polling loop to avoid triggering the rate limiter.
"""

import asyncio
from dataclasses import dataclass
from typing import Literal

import httpx
import keyring

from analecta.config import AppConfig

_VT_BASE = "https://www.virustotal.com/api/v3"
_KEYRING_SERVICE = "analecta"
_KEYRING_KEY = "VIRUSTOTAL_API_KEY"
_POLL_INTERVAL = 15.0  # seconds — keeps calls within 4 req/min Public API limit
_MAX_POLLS = 10  # ~2.5 minutes maximum wait


class VirusTotalError(Exception):
    """Base class for all VirusTotal errors."""


class VirusTotalKeyError(VirusTotalError):
    """API key not found in the system keyring."""


class VirusTotalRateLimitError(VirusTotalError):
    """Server returned HTTP 429 Too Many Requests."""


class VirusTotalTimeoutError(VirusTotalError):
    """Analysis did not reach ``completed`` status within the polling window."""


@dataclass
class ScanResult:
    """Result of a completed VirusTotal URL analysis.

    Attributes:
        url: The submitted URL.
        analysis_id: VirusTotal analysis identifier.
        malicious: Number of engines that flagged the URL as malicious.
        suspicious: Number of engines that flagged the URL as suspicious.
        undetected: Number of engines that found nothing.
        harmless: Number of engines that explicitly marked the URL safe.
    """

    url: str
    analysis_id: str
    malicious: int
    suspicious: int
    undetected: int
    harmless: int

    @property
    def total(self) -> int:
        """Total number of engines that processed the URL."""
        return self.malicious + self.suspicious + self.undetected + self.harmless

    @property
    def verdict(self) -> Literal["clean", "suspicious", "malicious"]:
        """Aggregated verdict derived from engine counts.

        Returns:
            ``"malicious"`` if any engine flagged it malicious, ``"suspicious"``
            if any flagged it suspicious, otherwise ``"clean"``.
        """
        if self.malicious > 0:
            return "malicious"
        if self.suspicious > 0:
            return "suspicious"
        return "clean"


class VirusTotalScanner:
    """Async VirusTotal URL scanner using the Public API v3.

    Enforces a ``poll_interval`` between each polling request to stay within
    the Public API rate limit (4 req/min). The default is 15 seconds.

    Args:
        poll_interval: Seconds to wait between each poll request.
        max_polls: Maximum number of poll attempts before raising
            ``VirusTotalTimeoutError``.
    """

    def __init__(
        self,
        poll_interval: float = _POLL_INTERVAL,
        max_polls: int = _MAX_POLLS,
    ) -> None:
        self._poll_interval = poll_interval
        self._max_polls = max_polls

    async def scan(self, url: str) -> ScanResult:
        """Submit *url* to VirusTotal and return the completed analysis.

        Args:
            url: URL to scan.

        Returns:
            Populated ``ScanResult`` with engine counts and verdict.

        Raises:
            VirusTotalKeyError: If the API key is absent from the keyring.
            VirusTotalRateLimitError: If the API returns HTTP 429.
            VirusTotalTimeoutError: If analysis does not complete in time.
            httpx.HTTPStatusError: For any other non-2xx API response.
        """
        api_key = self._get_api_key()
        headers = {"x-apikey": api_key, "accept": "application/json"}

        async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
            analysis_id = await self._submit(url, client)
            attrs = await self._poll(analysis_id, client)

        stats = attrs.get("stats", {})
        return ScanResult(
            url=url,
            analysis_id=analysis_id,
            malicious=stats.get("malicious", 0),
            suspicious=stats.get("suspicious", 0),
            undetected=stats.get("undetected", 0),
            harmless=stats.get("harmless", 0),
        )

    async def _submit(self, url: str, client: httpx.AsyncClient) -> str:
        response = await client.post(f"{_VT_BASE}/urls", data={"url": url})
        if response.status_code == 429:
            raise VirusTotalRateLimitError("Rate limit exceeded on URL submission.")
        response.raise_for_status()
        return response.json()["data"]["id"]

    async def _poll(self, analysis_id: str, client: httpx.AsyncClient) -> dict:
        for _ in range(self._max_polls):
            await asyncio.sleep(self._poll_interval)
            response = await client.get(f"{_VT_BASE}/analyses/{analysis_id}")
            if response.status_code == 429:
                raise VirusTotalRateLimitError("Rate limit exceeded during polling.")
            response.raise_for_status()
            attrs = response.json()["data"]["attributes"]
            if attrs.get("status") == "completed":
                return attrs
        raise VirusTotalTimeoutError(
            f"Analysis {analysis_id!r} did not complete after {self._max_polls} polls."
        )

    def _get_api_key(self) -> str:
        key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY)
        if not key:
            raise VirusTotalKeyError(
                f"VirusTotal API key not found in keyring. "
                f"Set it with: keyring.set_password('{_KEYRING_SERVICE}', "
                f"'{_KEYRING_KEY}', '<YOUR_KEY>')"
            )
        return key


def is_available(config: AppConfig) -> bool:
    """Return ``True`` if VT scanning is enabled and the API key is present.

    Args:
        config: Loaded application configuration.

    Returns:
        ``True`` only when ``config.virustotal_enabled`` is set **and** the
        API key exists in the system keyring. Safe to call at any time; never
        raises.
    """
    if not config.virustotal_enabled:
        return False
    return bool(keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY))
