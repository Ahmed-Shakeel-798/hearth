from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from logging_config import setup_logging
from media_downloader import DownloadFailedError, InvalidURLError, MediaDownloader

setup_logging()

MEDIA_DIR = Path(__file__).parent / "media"

downloader = MediaDownloader(MEDIA_DIR)

app = FastAPI(title="Music App Server")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


class DownloadRequest(BaseModel):
    url: str


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

    return {
        "status": "completed",
        "title": result.title,
        "uploader": result.uploader,
        "duration": result.duration,
        "thumbnail": result.thumbnail,
        "file": f"/media/{result.path.name}",
    }
