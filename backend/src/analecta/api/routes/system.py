import logging
from importlib.metadata import version

from fastapi import APIRouter

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/system/health")
async def health() -> dict[str, str]:
    """Return sidecar health status.

    Returns:
        JSON with ``status`` and ``version`` fields.
    """
    return {"status": "ok", "version": version("analecta")}
