"""
Jazz-Album micro-API
────────────────────
GET /album?album=Kind%20of%20Blue&artist=Miles%20Davis  →  JSON profile

New fields:
  • tracks          – numbered list of every track
  • session_info    – single sentence about when/where it was recorded
  • standout_notes  – listener-highlight sentence (“Stand-out tracks …”)
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict
import httpx, os, asyncio, re, html
from urllib.parse import quote

# ── FastAPI app ─────────────────────────────────────────────────────────
app = FastAPI(
    title="Jazz Album Profile",
    version="2.0.0",
    servers=[{"url": "https://jazz-api-oulu.onrender.com"}],   # ← your Render URL
)

# ── response schema ────────────────────────────────────────────────────
class Profile(BaseModel):
    artist: Dict[str, str]        # name, born, died, area
    album: Dict[str, str]         # title, year, catalogue
    personnel: List[str]
    quotes: List[str]
    cover_url: str
    long_text: str
    tracks: List[str]
    session_info: str
    standout_notes: str

# ── endpoints & keys ───────────────────────────────────────────────────
MUSICBRAINZ = "https://musicbrainz.org/ws/2"
DISCOGS     = "https://api.discogs.com"
DC_TOKEN    = os.getenv("DISCOGS_TOKEN", "")

# ── helper: Cover Art Archive fallback ─────────────────────────────────
async def cover_from_caa(release_group_id: str) -> str:
    url = f"https://coverartarchive.org/release-group/{release_group_id}/front-500"
    async with httpx.AsyncClient() as c:
        r = await c.get(url, timeout=10)
    return url if r.status_code == 200 else ""

# ── helper: MusicBrainz full track list ────────────────────────────────
async def mb_tracklist(release_id: str) -> List[str]:
    url = f"{MUSICBRAINZ}/release/{release_id}?fmt=json&inc=media+recordings"
    async with httpx.AsyncClient() as c:
        data = (await c.get(url, timeout=15)).json()
    tracks = []
    for medium in data.get("media", []):
        for t in medium.get("tracks", []):
            tracks.append(f'{t["position"]}. {t["title"]}')
    return tracks

# ── helper: Wikipedia “recorded at …” sentence ─────────────────────────
async def wiki_session(album: str, artist: str) -> str:
    slug = quote(f"{album} ({artist} album)")
    url  = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
    async with httpx.AsyncClient(
        headers={"User-Agent": "penguin-jazz-guide/2.0"}
    ) as c:
        r = await c.get(url, timeout=10)
    if r.status_code != 200:
        return ""
    text = r.json().get("extract", "")
    for sent in text.split(". "):
        if "recorded" in sent or "studio" in sent:
            return sent.strip() + "."
    return ""

# ── helper: quick “stand-out tracks” scrape (DuckDuckGo HTML) ──────────
async def standout_from_web(album: str, artist: str) -> str:
    q = quote(f'"{album}" "{artist}" review')
    url = f"https://duckduckgo.com/html/?q={q}"
    async with httpx.AsyncClient() as c:
        page = (await c.get(url, timeout=10)).text
    m = re.search(r'>([^<]{0,120}standout[^<]{0,120})<', page, re.I)
    if not m:
        return ""
    return html.unescape(m.group(1)).strip()

# ── MusicBrainz basic calls ────────────────────────────────────────────
async def mb_release(album: str, artist: str) -> dict:
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

# ── Discogs master data ────────────────────────────────────────────────
async def discogs_master(album: str, artist: str) -> dict:
    headers = {"User-Agent": "penguin-jazz-guide/2.0"}
    params  = {
        "q": f"{album} {artist}",
        "type": "master",
        "per_page": 1,
        "token": DC_TOKEN,
    }
    async with httpx.AsyncClient(headers=headers) as c:
        search  = (await c.get(f"{DISCOGS}/database/search", params=params, timeout=15)).json()
        master_id = search["results"][0]["id"]
        master    = (await c.get(f"{DISCOGS}/masters/{master_id}", timeout=15)).json()
    return master

# ── main endpoint ──────────────────────────────────────────────────────
@app.get("/album", response_model=Profile)
async def album(album: str, artist: str):
    try:
        mb_release_data = await mb_release(album, artist)
    except Exception:
        raise HTTPException(404, "Album not found in MusicBrainz")

    # parallel tasks
    artist_id  = mb_release_data["artist-credit"][0]["artist"]["id"]
    tasks = await asyncio.gather(
        mb_artist_dates(artist_id),
        discogs_master(album, artist),
        mb_tracklist(mb_release_data["id"]),
        wiki_session(album, artist),
        standout_from_web(album, artist),
    )
    artist_info, disc, tracks, session_info, standout = tasks

    # year (use first-release date)
    year = (
        (mb_release_data.get("release-group") or {}).get("first-release-date", "")[:4]
        or mb_release_data.get("date", "")[:4]
    )

    # catalogue number
    catno = ""
    if mb_release_data.get("label-info"):
        catno = mb_release_data["label-info"][0].get("catalog-number", "")

    # personnel (main + sidemen)
    personnel = [
        f'{p["name"]} — {p.get("role","").strip() or "primary"}'
        for p in disc.get("artists", [])
    ] + [
        f'{p["name"]} — {p.get("role","").strip()}'
        for p in disc.get("extraartists", [])
    ] or ["Personnel not listed"]

    # cover art
    cover = next(
        (img["uri"] for img in disc.get("images", []) if img.get("type") == "primary"),
        ""
    )
    if not cover:
        rg_id = (mb_release_data.get("release-group") or {}).get("id", "")
        if rg_id:
            cover = await cover_from_caa(rg_id)

    # Discogs liner-note excerpt
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
        quotes=[],              # still blank unless you add a quote scraper
        cover_url=cover,
        long_text=notes,
        tracks=tracks,
        session_info=session_info,
        standout_notes=standout,
    )
