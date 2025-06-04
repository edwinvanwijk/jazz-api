"""
Jazz-Album micro-API
────────────────────
• GET /album?album=Kind%20of%20Blue&artist=Miles%20Davis
  → JSON with artist facts, catalogue no., personnel, cover URL, etc.

Designed for the Penguin-Jazz-Guide GPT hosted at
https://jazz-api-oulu.onrender.com
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict
import httpx, os, asyncio
from urllib.parse import quote

# ── FastAPI app ──────────────────────────────────────────────────────────
app = FastAPI(
    title="Jazz Album Profile",
    version="1.0.0",
    servers=[{"url": "https://jazz-api-oulu.onrender.com"}],  # ← Render URL
)

# ── response schema ──────────────────────────────────────────────────────
class Profile(BaseModel):
    artist: Dict[str, str]        # name, born, died, area
    album: Dict[str, str]         # title, year, catalogue
    personnel: List[str]
    quotes: List[str]
    cover_url: str
    long_text: str

# ── constants ────────────────────────────────────────────────────────────
MUSICBRAINZ = "https://musicbrainz.org/ws/2"
DISCOGS     = "https://api.discogs.com"
DC_TOKEN    = os.getenv("DISCOGS_TOKEN", "")

# ── helpers ──────────────────────────────────────────────────────────────
async def cover_from_caa(release_group_id: str) -> str:
    """Return a 500-px front cover from Cover Art Archive if available."""
    url = f"https://coverartarchive.org/release-group/{release_group_id}/front-500"
    async with httpx.AsyncClient() as c:
        resp = await c.get(url, timeout=10)
    return url if resp.status_code == 200 else ""

def spotify_search_link(text: str) -> str:
    """Generate an all-regions Spotify search link."""
    return f"https://open.spotify.com/search/{quote(text)}"

# ── MusicBrainz calls ────────────────────────────────────────────────────
async def mb_release(album: str, artist: str) -> dict:
    """Return first matching release incl. nested release-group & label info."""
    q   = f'release:"{album}" AND artist:"{artist}"'
    url = f"{MUSICBRAINZ}/release/?query={q}&fmt=json&limit=1&inc=release-groups+labels"
    async with httpx.AsyncClient() as c:
        data = (await c.get(url, timeout=15)).json()
    if not data.get("releases"):
        raise ValueError("No MB release")
    return data["releases"][0]

async def mb_artist_dates(artist_id: str) -> dict:
    url = f"{MUSICBRAINZ}/artist/{artist_id}?fmt=json&inc=area"
    async with httpx.AsyncClient() as c:
        data = (await c.get(url, timeout=15)).json()
    life = data.get("life-span", {})
    return {
        "born": life.get("begin", ""),
        "died": life.get("end", ""),
        "area": (data.get("area") or {}).get("name", ""),
    }

# ── Discogs calls ────────────────────────────────────────────────────────
async def discogs_master(album: str, artist: str) -> dict:
    """Search Discogs for a master entry, then return full master JSON."""
    headers = {"User-Agent": "penguin-jazz-guide/1.0"}
    params  = {
        "q": f"{album} {artist}",
        "type": "master",
        "per_page": 1,
        "token": DC_TOKEN,
    }
    async with httpx.AsyncClient(headers=headers) as c:
        search = (await c.get(f"{DISCOGS}/database/search", params=params, timeout=15)).json()
        master_id = search["results"][0]["id"]
        master    = (await c.get(f"{DISCOGS}/masters/{master_id}", timeout=15)).json()
    return master

# ── API route ────────────────────────────────────────────────────────────
@app.get("/album", response_model=Profile)
async def album(album: str, artist: str):
    try:
        mb_release_data = await mb_release(album, artist)
    except Exception:
        raise HTTPException(404, "Album not found in MusicBrainz")

    # fetch artist dates + discogs master in parallel
    artist_mbid = mb_release_data["artist-credit"][0]["artist"]["id"]
    artist_task  = asyncio.create_task(mb_artist_dates(artist_mbid))
    discogs_task = asyncio.create_task(discogs_master(album, artist))

    artist_info = await artist_task
    disc        = await discogs_task

    # year: prioritise release-group first-release-date
    year = (
        (mb_release_data.get("release-group") or {}).get("first-release-date", "")[:4]
        or mb_release_data.get("date", "")[:4]
    )

    # catalogue number (if MusicBrainz has one)
    catno = ""
    if mb_release_data.get("label-info"):
        catno = mb_release_data["label-info"][0].get("catalog-number", "")

    # personnel: combine main artists + extra artists from Discogs
    personnel = [
        f'{p["name"]} — {p.get("role","").strip() or "primary"}'
        for p in disc.get("artists", [])
    ] + [
        f'{p["name"]} — {p.get("role","").strip()}'
        for p in disc.get("extraartists", [])
    ]
    if not personnel:
        personnel = ["Personnel not listed"]

    # cover art: Discogs primary image → fallback to Cover Art Archive
    cover = next(
        (img["uri"] for img in disc.get("images", []) if img.get("type") == "primary"),
        ""
    )
    if not cover:
        rg_id = (mb_release_data.get("release-group") or {}).get("id", "")
        if rg_id:
            cover = await cover_from_caa(rg_id)

    # long_text: short Discogs notes (max 2000 chars)
    notes = " ".join(disc.get("notes", "").splitlines())[:2000]

    return Profile(
        artist={
            "name": artist,
            "born": artist_info["born"],
            "died": artist_info["died"],
            "area": artist_info["area"],
        },
        album={
            "title": album,
            "year": year,
            "catalogue": catno,
        },
        personnel=personnel,
        quotes=[],              # fill later with a quotes-scraper if desired
        cover_url=cover,
        long_text=notes,
    )
