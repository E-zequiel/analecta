import asyncio
import re

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


def _extract_video_id(url: str) -> str | None:
    for pattern in _VIDEO_ID_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


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

        transcript_data, lang = await asyncio.to_thread(
            self._fetch_transcript, video_id
        )

        return ExtractedContent(
            title=f"YouTube: {video_id}",
            html=self._to_html(transcript_data),
            url=url,
            source_type="youtube",
            metadata={"video_id": video_id, "language": lang},
        )

    def _fetch_transcript(self, video_id: str) -> tuple[list, str]:
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

    def _to_html(self, transcript_data: list) -> str:
        return "\n".join(f"<p>{entry.text}</p>" for entry in transcript_data)
