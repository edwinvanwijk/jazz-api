from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx, os, asyncio

app = FastAPI(
    title="Jazz Album Profile",
    version="1.0.0",
    servers=[{"url": "https://jazz-api-oulu.onrender.com"}]  # ← keep your URL
)

# ── data model ───────────────────────────────────────────────────────────

class Profile(BaseModel):
    artist: dict      # {name, born, died}
    album: dict       # {title, year, catalogue}
    personnel: list[str]
    quotes: list[str]
    cover_url: str
    long_text: str

# ── external APIs ────────────────────────────────────────────────────────
MUSICBRAINZ = "https://musicbrainz.org/ws/2"
DISCOGS     = "https://api.discogs.com"
DC_TOKEN    = os.getenv("DISCOGS_TOKEN")

async def mb_release(album, artist):
    q   = f'release:"{album}" AND artist:"{artist}"'
    url = f"{MUSICBRAINZ}/release/?query={q}&fmt=json&limit=1"
    async with httpx.AsyncClient() as c:
        return (await c.get(url, timeout=15)).json()["releases"][0]

# ★ 1  look up artist birth/death via MusicBrainz -------------------------
async def mb_artist_dates(artist_id):
    url = f"{MUSICBRAINZ}/artist/{artist_id}?fmt=json&inc=aliases"
    async with httpx.AsyncClient() as c:
        data = (await c.get(url, timeout=15)).json()
    life = data.get("life-span", {})
    return {
        "born": life.get("begin", ""),
        "died": life.get("end", ""),
        "area": (data.get("area") or {}).get("name", "")
    }

# ★ 2  find correct Discogs master id first, THEN fetch personnel ----------
async def discogs_master(album, artist):
    async with httpx
