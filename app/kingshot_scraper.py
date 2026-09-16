"""
kingshot_scraper.py
Módulo para buscar datos de jugadores de KingShot usando kingshot.com.br.
Extrae: nombre, avatar, nivel de Forno (ciudad), alianza y poder de combate.
"""

import logging
import re

import requests

logger = logging.getLogger(__name__)

SEARCH_URL = "https://kingshot.com.br/search"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://kingshot.com.br/en/",
}


def lookup_player(player_fid: str, kingdom: str = "") -> dict:
    """
    Busca un jugador en kingshot.com.br por su FID (ID de jugador).

    Flujo:
        GET https://kingshot.com.br/search?fid=<ID>&kingdom=<REINO>&lang=en
        La web devuelve HTML con el perfil del jugador.

    Args:
        player_fid: ID numérico del jugador (ej. "325963354")
        kingdom: Número de reino (ej. "2072") — mejora la precisión

    Returns dict con:
        found        : bool
        player_name  : str | None
        avatar_url   : str | None  (CDN got-global-avatar.akamaized.net)
        city_level   : int | None  (nivel de Forno, ej. 30)
        city_label   : str | None  (texto completo, ej. "Forno 30")
        alliance_name: str | None
        power        : str | None  (ej. "51,2M")
        kills        : str | None  (ej. "6,3M")
        kingdom      : str | None
        source       : "kingshot.com.br"
    """
    params = {"fid": player_fid, "lang": "en"}
    if kingdom:
        params["kingdom"] = kingdom

    try:
        resp = requests.get(
            SEARCH_URL,
            params=params,
            headers=HEADERS,
            timeout=8,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"[KingshotScraper] Error fetching player {player_fid}: {e}")
        return {"found": False, "error": str(e)}

    html = resp.text

    # ---------- Player NOT found ----------
    if 'data-lookup-result="not_found' in html or "Player not found" in html:
        return {"found": False, "player_id": player_fid}

    if 'data-lookup-result="found_multiple"' in html:
        return {"found": False, "player_id": player_fid, "error": "multiple_results"}

    # ---------- Build result ----------
    result = {
        "found": True,
        "player_id": player_fid,
        "player_name": None,
        "avatar_url": None,
        "city_level": None,
        "city_label": None,
        "alliance_name": None,
        "power": None,
        "kills": None,
        "kingdom": kingdom or None,
        "source": "kingshot.com.br",
    }

    # --- Player name: <h1> inside player-profile-identity ---
    name_match = re.search(
        r'class="player-profile-identity"[^>]*>.*?<h1>(.*?)</h1>',
        html,
        re.DOTALL,
    )
    if name_match:
        result["player_name"] = name_match.group(1).strip()

    # Fallback: OG title format "NaGaSiS - Poder, reino XXXX..."
    if not result["player_name"]:
        og_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        if og_match:
            title = og_match.group(1)
            candidate = title.split(" - ")[0].strip()
            if candidate and "Kingshot" not in candidate:
                result["player_name"] = candidate

    # --- Avatar: got-global-avatar.akamaized.net ---
    avatar_match = re.search(
        r'class="profile-photo[^"]*"[^>]*>.*?<img\s+src="(https://got-global-avatar\.akamaized\.net/[^"]+)"',
        html,
        re.DOTALL,
    )
    if avatar_match:
        result["avatar_url"] = avatar_match.group(1)

    if not result["avatar_url"]:
        avatar_match2 = re.search(
            r'src="(https://got-global-avatar\.akamaized\.net/avatar/[^"]+)"', html
        )
        if avatar_match2:
            result["avatar_url"] = avatar_match2.group(1)

    # --- City/TC level ---
    # KingShot has two building tiers: "Forno X" (levels 1-30) and "TG X" (post-30).
    # The kpi-card shows the current label directly (e.g. "TG1", "Forno 30").
    # We normalize "Forno" → "TC" but keep "TG" labels intact.

    # Pattern 1: kpi-card "Forno/TG" -> <strong>TG1</strong> or <strong>Forno 30</strong>
    kpi_match = re.search(
        r"Forno/TG.*?<strong>([^<]+)</strong>",
        html,
        re.DOTALL,
    )
    if kpi_match:
        raw_label = kpi_match.group(1).strip()  # e.g. "TG1" or "Forno 30"
        num_match = re.search(r"\d+", raw_label)
        if num_match:
            level = int(num_match.group())
            result["city_level"] = level
            # Keep TG prefix as-is; normalize Forno -> TC
            if raw_label.upper().startswith("TG"):
                result["city_label"] = raw_label.upper()  # "TG1", "TG2", etc.
            else:
                result["city_label"] = f"TC {level}"  # Forno 30 -> TC 30

    # Pattern 2: badge element <span class="ks-tg-badge__text">TG1</span>
    if not result["city_level"]:
        badge_match = re.search(
            r'class="ks-tg-badge__text"\s*>\s*([^<]+)\s*<', html
        )
        if badge_match:
            raw_label = badge_match.group(1).strip()
            num_match = re.search(r"\d+", raw_label)
            if num_match:
                level = int(num_match.group())
                result["city_level"] = level
                if raw_label.upper().startswith("TG"):
                    result["city_label"] = raw_label.upper()
                else:
                    result["city_label"] = f"TC {level}"

    # --- Alliance ---
    alliance_match = re.search(
        r'class="player-profile-alliance">(.*?)</p>', html, re.DOTALL
    )
    if alliance_match:
        result["alliance_name"] = alliance_match.group(1).strip()

    # --- Power ---
    power_match = re.search(
        r'class="profile-power[^"]*"[^>]*>.*?<strong>([\d,\.]+\s*[MKBmkb]?)</strong>',
        html,
        re.DOTALL,
    )
    if power_match:
        result["power"] = power_match.group(1).strip()

    # --- Kills: kpi-card "Kills" -> <strong>6,3M</strong> ---
    kills_match = re.search(
        r">Kills<.*?<strong>([\d,\.]+\s*[MKBmkb]?)</strong>",
        html,
        re.DOTALL,
    )
    if kills_match:
        val = kills_match.group(1).strip()
        if "não coletado" not in val.lower():
            result["kills"] = val

    # --- Kingdom (from meta) ---
    if not result["kingdom"]:
        kingdom_match = re.search(r"ID\s+\d+\s+·\s+Reino\s+(\d+)", html)
        if kingdom_match:
            result["kingdom"] = kingdom_match.group(1)

    return result
