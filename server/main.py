from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from logging_config import setup_logging
from media_downloader import DownloadFailedError, InvalidURLError, MediaDownloader
from media_repository import Media, MediaRepository

setup_logging()

SERVER_DIR = Path(__file__).parent
MEDIA_DIR = SERVER_DIR / "media"
DB_PATH = SERVER_DIR / "hearth.db"

downloader = MediaDownloader(MEDIA_DIR)
repository = MediaRepository(DB_PATH)

app = FastAPI(title="Music App Server")


class DownloadRequest(BaseModel):
    url: str


class MediaSummary(BaseModel):
    """One item in the library listing."""

    id: int
    title: str
    creator: str | None
    duration: int | None
    thumbnail: str | None


class MediaDetail(MediaSummary):
    """Full metadata for one item. Exposes a URL, not the on-disk path."""

    source_url: str
    file_size: int
    mime_type: str
    created_at: str
    file_url: str


def _to_detail(media: Media) -> MediaDetail:
    return MediaDetail(
        id=media.id,
        title=media.title,
        creator=media.creator,
        duration=media.duration,
        thumbnail=media.thumbnail,
        source_url=media.source_url,
        file_size=media.file_size,
        mime_type=media.mime_type,
        created_at=media.created_at,
        file_url=f"/media/{media.id}/file",
    )


def _get_or_404(media_id: int) -> Media:
    media = repository.get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail=f"No media with id {media_id}")
    return media


@app.get("/health")
def health():
    return {"status": "ok"}


# Plain `def` (not `async def`): FastAPI runs it in a worker thread,
# so the blocking download doesn't freeze the server.
@app.post("/downloads")
def create_download(req: DownloadRequest):
    try:
        result = downloader.download(req.url)
    except InvalidURLError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except DownloadFailedError as e:
        raise HTTPException(status_code=400, detail=str(e))

    media = repository.add(
        title=result.title,
        creator=result.uploader,
        source_url=result.source_url,
        duration=result.duration,
        thumbnail=result.thumbnail,
        # Relative, so the database survives the media folder being moved
        file_path=result.path.relative_to(MEDIA_DIR).as_posix(),
        file_size=result.file_size,
        mime_type=result.mime_type,
    )

    return {"status": "completed", "media": _to_detail(media)}


@app.get("/media", response_model=list[MediaSummary])
def list_media():
    return [
        MediaSummary(
            id=m.id,
            title=m.title,
            creator=m.creator,
            duration=m.duration,
            thumbnail=m.thumbnail,
        )
        for m in repository.list()
    ]


@app.get("/media/{media_id}", response_model=MediaDetail)
def get_media(media_id: int):
    return _to_detail(_get_or_404(media_id))


@app.get("/media/{media_id}/file")
def get_media_file(media_id: int):
    media = _get_or_404(media_id)
    path = MEDIA_DIR / media.file_path
    if not path.is_file():
        # The row exists but the file was deleted or moved on disk
        raise HTTPException(status_code=404, detail="File missing on disk")

    # FileResponse streams from disk and supports Range requests,
    # so players can seek without downloading the whole file.
    # "inline" lets browsers play it rather than forcing a download.
    return FileResponse(
        path,
        media_type=media.mime_type,
        filename=path.name,
        content_disposition_type="inline",
    )
