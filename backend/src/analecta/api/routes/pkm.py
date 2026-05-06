from fastapi import APIRouter

from analecta.pkm.url_scheme import parse_url

router = APIRouter()


@router.get("/pkm/parse-url")
async def pkm_parse_url(url: str) -> dict[str, int | None]:
    """Parse an ``analecta://`` URL and return its entry id.

    The ``id`` query parameter is validated as a positive integer.
    Returns ``null`` for any URL that does not conform to the scheme.

    Args:
        url: Candidate ``analecta://`` URL string.

    Returns:
        ``{"entry_id": <int>}`` on success or ``{"entry_id": null}`` if invalid.
    """
    return {"entry_id": parse_url(url)}
