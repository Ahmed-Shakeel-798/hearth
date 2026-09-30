import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

log = logging.getLogger(__name__)


class DownloadError(Exception):
    """Base error for anything that goes wrong in MediaDownloader."""


class InvalidURLError(DownloadError):
    """The URL is malformed or not http(s)."""


class DownloadFailedError(DownloadError):
    """yt-dlp or FFmpeg could not produce a file."""


@dataclass
class DownloadResult:
    id: str
    title: str
    uploader: str | None
    duration: int | None  # seconds
    thumbnail: str | None
    source_url: str
    path: Path  # where the final audio file was saved


class MediaDownloader:
    """Downloads a URL's audio track to a local directory.

    Wraps yt-dlp so the rest of the app never touches it directly.
    """

    AUDIO_CODEC = "m4a"

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def download(self, url: str) -> DownloadResult:
        self._validate_url(url)
        log.info("Request received: %s", url)

        try:
            with yt_dlp.YoutubeDL(self._options(_ProgressLogger())) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            log.error("Download failed: %s", e)
            raise DownloadFailedError(str(e)) from e

        result = self._build_result(url, info)
        log.info("[%s] Saved %s", result.id, result.path.name)
        return result

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise InvalidURLError(f"Not a valid http(s) URL: {url!r}")

    def _options(self, progress: "_ProgressLogger") -> dict:
        return {
            # Audio selection: prefer a stream already in the target codec so
            # FFmpeg can skip re-encoding; fall back to best audio, then anything
            "format": f"bestaudio[ext={self.AUDIO_CODEC}]/bestaudio/best",
            # Filename: title-based, ASCII only, no spaces
            "outtmpl": str(self.output_dir / "%(title)s.%(ext)s"),
            "restrictfilenames": True,
            "noplaylist": True,
            # FFmpeg converts the downloaded stream to the target codec
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": self.AUDIO_CODEC},
            ],
            # Route yt-dlp's own output through our logger, and report
            # download/conversion milestones via hooks
            "logger": _YtDlpLogger(),
            "progress_hooks": [progress.on_download],
            "postprocessor_hooks": [progress.on_postprocess],
        }

    def _build_result(self, url: str, info: dict) -> DownloadResult:
        # After post-processing, yt-dlp records the final path here
        downloads = info.get("requested_downloads") or []
        if not downloads or "filepath" not in downloads[0]:
            raise DownloadFailedError("yt-dlp did not report an output file")

        return DownloadResult(
            id=info["id"],
            title=info.get("title") or info["id"],
            uploader=info.get("uploader"),
            duration=info.get("duration"),
            thumbnail=info.get("thumbnail"),
            source_url=url,
            path=Path(downloads[0]["filepath"]),
        )


class _ProgressLogger:
    """Turns yt-dlp's hook callbacks into a few milestone log lines.

    One instance per download, so concurrent downloads don't share state.
    """

    def __init__(self):
        self._download_started = False
        self._convert_started_at = 0.0
        self._convert_seen: set[str] = set()

    def on_download(self, d: dict) -> None:
        video_id = d["info_dict"].get("id")

        # "downloading" fires on every chunk; only log the first one
        if d["status"] == "downloading" and not self._download_started:
            self._download_started = True
            size = d.get("total_bytes") or d.get("total_bytes_estimate")
            log.info(
                "[%s] Downloading %r (%s)",
                video_id,
                d["info_dict"].get("title"),
                _format_size(size),
            )
        elif d["status"] == "finished":
            elapsed = d.get("elapsed")
            log.info(
                "[%s] Download finished%s",
                video_id,
                f" in {elapsed:.1f}s" if elapsed else "",
            )

    def on_postprocess(self, d: dict) -> None:
        if d["postprocessor"] != "ExtractAudio":
            return  # ignore yt-dlp's internal bookkeeping steps

        # yt-dlp can deliver each postprocessor event more than once
        status = d["status"]
        if status in self._convert_seen:
            return
        self._convert_seen.add(status)

        video_id = d["info_dict"].get("id")
        if status == "started":
            self._convert_started_at = time.monotonic()
            log.info("[%s] Converting to %s", video_id, MediaDownloader.AUDIO_CODEC)
        elif status == "finished":
            elapsed = time.monotonic() - self._convert_started_at
            log.info("[%s] Conversion finished in %.1fs", video_id, elapsed)


class _YtDlpLogger:
    """Receives yt-dlp's console output.

    Its chatter is dropped and errors are logged by MediaDownloader when
    it catches them, so only warnings are passed through.
    """

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        log.warning(msg)

    def error(self, msg: str) -> None:
        pass


def _format_size(num_bytes: int | None) -> str:
    if not num_bytes:
        return "unknown size"
    return f"{num_bytes / 1_000_000:.1f} MB"
