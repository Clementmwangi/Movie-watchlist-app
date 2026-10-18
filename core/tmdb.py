"""Thin TMDB client. Never trust media metadata from the browser: the server re-fetches by ID."""
import requests
from django.conf import settings
from django.core.cache import cache

BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p"


class TMDBError(Exception):
    pass


def _get(path, **params):
    key = settings.TMDB_API_KEY
    if not key:
        raise TMDBError("TMDB_API_KEY is not set.")
    headers = {}
    if key.startswith("eyJ"):  # v4 read-access token
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    try:
        resp = requests.get(f"{BASE}{path}", params=params, headers=headers, timeout=8)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise TMDBError("TMDB request failed.") from exc
    return resp.json()


def search(query):
    query = query.strip()
    if len(query) < 2:
        return []
    ck = f"tmdb:search:{query.lower()}"
    hit = cache.get(ck)
    if hit is not None:
        return hit
    data = _get("/search/multi", query=query, include_adult="false")
    results = []
    for r in data.get("results", []):
        if r.get("media_type") not in ("movie", "tv"):
            continue
        date = r.get("release_date") or r.get("first_air_date") or ""
        results.append({
            "tmdb_id": r["id"],
            "media_type": r["media_type"],
            "title": r.get("title") or r.get("name") or "Untitled",
            "year": date[:4],
            "poster_path": r.get("poster_path"),
            "overview": (r.get("overview") or "")[:180],
        })
    results = results[:10]
    cache.set(ck, results, 600)
    return results


def details(media_type, tmdb_id):
    """Returns normalized metadata plus a season->episode_count layout."""
    if media_type not in ("movie", "tv"):
        raise TMDBError("Unknown media type.")
    d = _get(f"/{media_type}/{int(tmdb_id)}")
    out = {
        "title": d.get("title") or d.get("name") or "Untitled",
        "poster_path": d.get("poster_path"),
        "backdrop_path": d.get("backdrop_path"),
        "overview": d.get("overview") or "",
    }
    if media_type == "movie":
        out["seasons"] = [(1, 1)]
        out["runtime"] = d.get("runtime")
    else:
        # Season 0 is "Specials" on TMDB; counting it would inflate the workload.
        out["seasons"] = [
            (s["season_number"], s["episode_count"])
            for s in d.get("seasons", [])
            if s.get("season_number", 0) > 0 and s.get("episode_count", 0) > 0
        ]
        out["runtime"] = (d.get("episode_run_time") or [None])[0]
    out["total_episodes"] = sum(n for _, n in out["seasons"]) or 1
    if not out["seasons"]:
        out["seasons"] = [(1, 1)]
    return out
