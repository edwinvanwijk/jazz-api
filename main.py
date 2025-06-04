# main.py  (already in the repo)
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx, os

app = FastAPI(
    title="Jazz Album Profile",
    version="1.0.0",
    servers=[{"url": "https://jazz-api-oulu.onrender.com"}]   
)


class Profile(BaseModel):
    artist: dict
    album: dict
    personnel: list[str]
    quotes: list[str]
    long_text: str
    cover_url: str

MUSICBRAINZ = "https://musicbrainz.org/ws/2"
DISCOGS     = "https://api.discogs.com"

async def mb_release(album, artist):
    url = f'{MUSICBRAINZ}/release/?query=release:"{album}" AND artist:"{artist}"&fmt=json&limit=1'
    async with httpx.AsyncClient() as c:
        data = (await c.get(url, timeout=15)).json()
        return data["releases"][0]

async def discogs_master(master_id):
    token = os.getenv("DISCOGS_TOKEN")
    headers = {"Authorization": f"Discogs token={token}"} if token else {}
    async with httpx.AsyncClient(headers=headers) as c:
        return (await c.get(f"{DISCOGS}/masters/{master_id}", timeout=15)).json()

@app.get("/album", response_model=Profile)
async def album(album: str, artist: str):
    try:
        mb = await mb_release(album, artist)
    except Exception:
        raise HTTPException(404, "Album not found")

    disc = await discogs_master(mb["id"])
    return Profile(
        artist={"name": artist},
        album={
            "title": album,
            "year": mb.get("date", "")[:4],
            "catalogue": disc.get("main_release_url", "").split("/")[-1],
            "musicbrainz_id": mb["id"],
            "discogs_master": disc.get("id"),
        },
        personnel=[
            f'{p["name"]} — {p.get("role","")}'
            for p in disc.get("extraartists", [])
        ],
        quotes=[],
        long_text=" ".join(disc.get("notes", "").splitlines())[:2000],
            cover_url = next(
                (img["uri"] for img in disc.get("images", []) if img.get("type") == "primary"),
                ""
            )
    )
