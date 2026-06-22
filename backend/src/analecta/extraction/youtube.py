import asyncio
import re
from typing import Any

import httpx2
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from analecta.extraction.core import ExtractedContent, ExtractionError, SourceExtractor

_VIDEO_ID_PATTERNS = [
    r"youtube\.com/watch\?(?:.*&)?v=([^&]+)",
    r"youtu\.be/([^?/]+)",
    r"youtube\.com/embed/([^?/]+)",
]

_OEMBED_URL = "https://www.youtube.com/oembed"
_HEADERS = {"User-Agent": "Analecta/0.3"}
_TIMEOUT = 8.0


def _extract_video_id(url: str) -> str | None:
    for pattern in _VIDEO_ID_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


async def _fetch_video_title(video_id: str) -> tuple[str, str | None]:
    """Fetch video title and channel name via YouTube oEmbed.

    Args:
        video_id: YouTube video ID.

    Returns:
        Tuple of (title, author_name). Falls back to ``"YouTube: {video_id}"``
        on any network or parsing error.
    """
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        async with httpx2.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT) as client:
            resp = await client.get(
                _OEMBED_URL, params={"url": watch_url, "format": "json"}
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            title = data.get("title") or f"YouTube: {video_id}"
            return title, data.get("author_name")
    except Exception:
        return f"YouTube: {video_id}", None


class YouTubeExtractor(SourceExtractor):
    """Extracts transcripts from YouTube videos via ``youtube-transcript-api``.

    If no transcript is available, raises ``ExtractionError`` with a
    descriptive message so the caller can surface a notification to the user.
    """

    async def extract(self, url: str) -> ExtractedContent:
        """Fetch the transcript for a YouTube video URL.

        Args:
            url: YouTube watch, short, or embed URL.

        Returns:
            ``ExtractedContent`` with transcript HTML and ``source_type="youtube"``.

        Raises:
            ExtractionError: If the video ID cannot be parsed or no transcript exists.
        """
        video_id = _extract_video_id(url)
        if not video_id:
            raise ExtractionError(f"Cannot parse video ID from URL: {url}")

        (title, author_name), (transcript_data, lang) = await asyncio.gather(
            _fetch_video_title(video_id),
            asyncio.to_thread(self._fetch_transcript, video_id),
        )

        meta: dict[str, Any] = {"video_id": video_id, "language": lang}
        if author_name:
            meta["author"] = author_name

        return ExtractedContent(
            title=title,
            html=self._to_html(transcript_data),
            url=url,
            source_type="youtube",
            metadata=meta,
        )

    def _fetch_transcript(self, video_id: str) -> tuple[list[Any], str]:
        try:
            listing = YouTubeTranscriptApi().list(video_id)
            try:
                transcript = listing.find_transcript(["en", "es"])
            except NoTranscriptFound:
                transcript = next(iter(listing))
            data = list(transcript.fetch())
            return data, transcript.language_code
        except TranscriptsDisabled as exc:
            raise ExtractionError(
                f"Transcripts are disabled for video {video_id}."
            ) from exc
        except StopIteration as exc:
            raise ExtractionError(
                f"No transcripts available for video {video_id}."
            ) from exc

    def _to_html(self, transcript_data: list[Any]) -> str:
        return "\n".join(f"<p>{entry.text}</p>" for entry in transcript_data)
