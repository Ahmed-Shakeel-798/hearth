from pathlib import Path

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

MEDIA_DIR = Path(__file__).parent / "media"
MEDIA_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Music App Server")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


class DownloadRequest(BaseModel):
    url: HttpUrl  # validation: rejects anything that isn't an http(s) URL


@app.get("/health")
def health():
    return {"status": "ok"}


# Plain `def` (not `async def`): FastAPI runs it in a worker thread,
# so the blocking download doesn't freeze the server.
@app.post("/downloads")
def create_download(req: DownloadRequest):
    options = {
        # yt-dlp: pick the best audio-only stream
        "format": "bestaudio/best",
        "outtmpl": str(MEDIA_DIR / "%(title)s.%(ext)s"),
        "restrictfilenames": True,  # ASCII only, no spaces
        "noplaylist": True,
        # FFmpeg: convert whatever was downloaded into .m4a
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"},
        ],
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(str(req.url), download=True)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Path of the final file, after FFmpeg has run
    saved = Path(info["requested_downloads"][0]["filepath"])

    return {
        "status": "completed",
        "title": info.get("title"),
        "file": f"/media/{saved.name}",
    }
