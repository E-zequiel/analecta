import asyncio
import logging

import keyring
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from analecta.api.deps import get_config, get_event_bus, get_index
from analecta.config import AppConfig
from analecta.security.virustotal import (
    VirusTotalKeyError,
    VirusTotalRateLimitError,
    VirusTotalScanner,
)
from analecta.storage.index import VaultIndex

log = logging.getLogger(__name__)
router = APIRouter()

_KEYRING_SERVICE = "analecta"
_KEYRING_KEY = "VIRUSTOTAL_API_KEY"


class KeyIn(BaseModel):
    """Body for PUT /security/virustotal/key.

    Attributes:
        value: API key value to store in the system keyring.
    """

    value: str


class ScanIn(BaseModel):
    """Body for POST /security/virustotal/scan.

    Attributes:
        entry_id: Database row id of the entry whose URL should be scanned.
    """

    entry_id: int


class ScanOut(BaseModel):
    """Result returned after a completed VirusTotal scan.

    Attributes:
        entry_id: Entry that was scanned.
        verdict: Aggregated verdict (``clean``, ``suspicious``, or ``malicious``).
        malicious: Count of engines that flagged the URL as malicious.
        suspicious: Count of engines that flagged the URL as suspicious.
        undetected: Count of engines that found nothing.
        harmless: Count of engines that marked the URL safe.
        total: Total engines that processed the URL.
    """

    entry_id: int
    verdict: str
    malicious: int
    suspicious: int
    undetected: int
    harmless: int
    total: int


@router.get("/security/virustotal/key/exists")
async def key_exists() -> dict[str, bool]:
    """Check whether a VirusTotal API key is stored in the keyring.

    Returns:
        ``{"exists": true}`` if a key is present, ``{"exists": false}`` otherwise.
        Never returns the key value itself.
    """
    key = await asyncio.to_thread(keyring.get_password, _KEYRING_SERVICE, _KEYRING_KEY)
    return {"exists": bool(key)}


@router.put("/security/virustotal/key", status_code=204)
async def set_key(body: KeyIn) -> None:
    """Store a VirusTotal API key in the system keyring.

    Args:
        body: Body containing the API key value.
    """
    await asyncio.to_thread(
        keyring.set_password, _KEYRING_SERVICE, _KEYRING_KEY, body.value
    )


@router.post("/security/virustotal/scan", response_model=ScanOut)
async def scan_entry(
    body: ScanIn,
    index: VaultIndex = Depends(get_index),
    event_bus: "asyncio.Queue[dict[str, object]]" = Depends(get_event_bus),
    config: AppConfig = Depends(get_config),
) -> ScanOut:
    """Submit an entry's URL to VirusTotal and return the analysis result.

    Emits ``scan_progress`` and ``scan_completed`` events to the SSE bus.

    Args:
        body: Body containing the entry id to scan.
        index: Injected VaultIndex singleton.
        event_bus: Injected SSE event bus.
        config: Injected AppConfig singleton.

    Returns:
        Scan result with engine counts and aggregated verdict.

    Raises:
        HTTPException: 403 if VT scanning is not enabled or the API key is missing.
        HTTPException: 404 if the entry does not exist.
        HTTPException: 429 if the VirusTotal rate limit is exceeded.
    """
    if not config.virustotal_enabled:
        raise HTTPException(status_code=403, detail="VirusTotal scanning is not enabled")

    entry = await asyncio.to_thread(index.get_entry, body.entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    await event_bus.put({"type": "scan_progress", "entry_id": body.entry_id, "status": "started"})

    try:
        result = await VirusTotalScanner().scan(entry.url)
    except VirusTotalKeyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except VirusTotalRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    await event_bus.put(
        {"type": "scan_completed", "entry_id": body.entry_id, "verdict": result.verdict}
    )
    return ScanOut(
        entry_id=body.entry_id,
        verdict=result.verdict,
        malicious=result.malicious,
        suspicious=result.suspicious,
        undetected=result.undetected,
        harmless=result.harmless,
        total=result.total,
    )
