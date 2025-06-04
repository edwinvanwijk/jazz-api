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
    async with httpx.AsyncClient() as c:
        # search
        s_url = f"{DISCOGS}/database/search"
        params = {
            "q": f"{album} {artist}",
            "type": "master",
            "per_page": 1,
            "token": DC_TOKEN
        }
        search = (await c.get(s_url, params=params, timeout=15)).json()
        master_id = search["results"][0]["id"]
        # master detail
        m_url = f"{DISCOGS}/masters/{master_id}"
        return (await c.get(m_url, timeout=15)).json()

# ── route ────────────────────────────────────────────────────────────────
@app.get("/album", response_model=Profile)
async def album(album: str, artist: str):
    try:
        mb  = await mb_release(album, artist)
    except Exception:
        raise HTTPException(404, "Album not found")

    # gather in parallel
    artist_task   = asyncio.create_task(mb_artist_dates(mb["artist-credit"][0]["artist"]["id"]))
    discogs_task  = asyncio.create_task(discogs_master(album, artist))
    artist_info   = await artist_task
    disc          = await discogs_task

    # common helpers
    catno = ""
    if mb.get("label-info"):
        catno = mb["label-info"][0].get("catalog-number", "")
    # personnel list
    personnel = [
        f'{p["name"]} — {p.get("role","")}'
        for p in disc.get("extraartists", [])
    ] or [m.get("name") for m in disc.get("artists", [])]

    # cover art
    cover = next(
        (img["uri"] for img in disc.get("images", []) if img.get("type") == "primary"),
        ""
    )

    # return
    return Profile(
        artist={
            "name": artist,
            "born": artist_info["born"],
            "died": artist_info["died"],
            "area": artist_info["area"],
        },
        album={
            "title": album,
            "year": mb.get("date", "")[:4],
            "catalogue": catno,
        },
        personnel=personnel,
        quotes=[],           # you can fill this later with a scraper
        cover_url=cover,
        long_text=" ".join(disc.get("notes", "").splitlines())[:2000],
    )
