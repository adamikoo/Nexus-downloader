import os
import re
import json
import urllib.request
import urllib.parse
from http.server import SimpleHTTPRequestHandler, HTTPServer
import threading
import sys
import ssl

# Global SSL verification bypass for older local Python installations
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    pass
try:
    import pycurl
    HAS_PYCURL = True
except ImportError:
    HAS_PYCURL = False

try:
    import tkinter as tk
    from tkinter import filedialog
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False

PORT = 8000
API_BASE_URL = "https://api.nexusmods.com/v1"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"

# Thread-safe download tracking
downloads_state = {}
downloads_lock = threading.Lock()

# Config storage
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                if "games" not in data:
                    data["games"] = []
                if "sid_cookie" not in data:
                    data["sid_cookie"] = ""
                return data
        except Exception:
            pass
    return {"api_key": "", "sid_cookie": "", "custom_paths": [], "games": []}

def save_config(config):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
        return True
    except Exception:
        return False

def sanitize_match(text):
    return re.sub(r'[^a-z0-9]', '', text.lower()) if text else ""

def extract_url_from_json(data):
    if isinstance(data, str) and data.startswith("http"):
        return data
    if isinstance(data, list):
        for item in data:
            res = extract_url_from_json(item)
            if res:
                return res
    if isinstance(data, dict):
        for k in ["URI", "url", "URL", "link"]:
            if k in data:
                res = extract_url_from_json(data[k])
                if res:
                    return res
        for k, v in data.items():
            res = extract_url_from_json(v)
            if res:
                return res
    return None

# Nexus Mods numeric game IDs (required by Offline/DownloadLink endpoint)
NEXUS_GAME_NUMERIC_IDS = {
    "readyornot": 3953,
    "skyrimspecialedition": 1704,
    "cyberpunk2077": 3333,
    "fallout4": 1151,
    "witcher3": 952,
    "bmxstreets": 5406,
    "stalker2heartofchornobyl": 6949,
    "dragonsdogma2": 4315,
    "eldenring": 4982,
}

def _cffi_get(url, cookie_header, game_id, mod_id, allow_redirects=False):
    """HTTP GET using curl_cffi Chrome impersonation (bypasses Cloudflare TLS fingerprint check)."""
    try:
        from curl_cffi import requests as cf_requests
        resp = cf_requests.get(
            url,
            headers={
                "Cookie": cookie_header,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": f"https://www.nexusmods.com/{game_id}/mods/{mod_id}?tab=files",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            },
            impersonate="chrome124",
            allow_redirects=allow_redirects,
            timeout=15,
        )
        return resp
    except Exception as e:
        return None

def _cffi_post(url, data, cookie_header, game_id, mod_id):
    """HTTP POST using curl_cffi Chrome impersonation (bypasses Cloudflare TLS fingerprint check)."""
    try:
        from curl_cffi import requests as cf_requests
        resp = cf_requests.post(
            url,
            data=data,
            headers={
                "Cookie": cookie_header,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": f"https://www.nexusmods.com/{game_id}/mods/{mod_id}?tab=files",
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Origin": "https://www.nexusmods.com",
            },
            impersonate="chrome124",
            timeout=15,
        )
        return resp
    except Exception as e:
        return None

def _extract_download_url_from_html(html, file_id):
    """Aggressively search HTML/JS for any Nexus CDN or time-limited download URL."""
    patterns = [
        # Direct CDN domains
        re.compile(r'["\']?(https://filedelivery\.nexusmods\.com/[^"\'<>\s]+)["\']?'),
        re.compile(r'["\']?(https://[^"\'<>\s]*nexus-cdn\.com/[^"\'<>\s]+)["\']?'),
        re.compile(r'["\']?(https://[^"\'<>\s]*cf-files\.nexusmods\.com/[^"\'<>\s]+)["\']?'),
        # Time-limited download tokens (key= and expires= in URL)
        re.compile(r'["\']?(https://[^"\'<>\s]+\?[^"\'<>\s]*key=[^"\'<>\s]+expires=[^"\'<>\s]+)["\']?'),
        re.compile(r'["\']?(https://[^"\'<>\s]+\?[^"\'<>\s]*expires=[^"\'<>\s]+key=[^"\'<>\s]+)["\']?'),
        # data-url / data-link / href attributes containing download
        re.compile(r'data-(?:url|link|download)=["\']?(https://[^"\'<>\s]+)["\']?'),
        # Any .zip/.rar/.7z/.pak URL with a token
        re.compile(r'["\']?(https://[^"\'<>\s]+\.(?:zip|rar|7z|pak|ba2|esm|esp)[^"\'<>\s]*)["\']?'),
    ]
    for pattern in patterns:
        m = pattern.search(html)
        if m:
            url = m.group(1).strip().rstrip("'\"")
            if url.startswith("http"):
                return url
    # Search JSON embedded in script tags
    script_json = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    for block in script_json:
        urls = re.findall(r'"(https://[^"]{20,})"', block)
        for u in urls:
            if "filedelivery" in u or "nexus-cdn" in u or ("key=" in u and "expires=" in u):
                return u
    return None

def get_nxm_download_url_via_nmm(file_id, game_id, mod_id, cookie_header, api_key):
    """Retrieve NXM download keys by requesting the NMM page (nmm=1) and calling the public API."""
    if not api_key:
        return None, "API Key is required for NMM resolution"
        
    nmm_url = (
        f"https://www.nexusmods.com/{game_id}/mods/{mod_id}"
        f"?tab=files&file_id={file_id}&nmm=1"
    )
    resp = _cffi_get(nmm_url, cookie_header, game_id, mod_id, allow_redirects=True)
    if resp is None:
        return None, "Failed to fetch NMM page (None response)"
        
    if resp.status_code != 200:
        return None, f"NMM page returned HTTP {resp.status_code}"
        
    # Search for any nxm:// link inside the HTML
    match = re.search(r'nxm://[^\s"\'<>]+', resp.text)
    if not match:
        return None, "No NXM protocol link found in NMM page HTML"
        
    nxm_url = match.group(0).replace("&amp;", "&")
    # Parse parameters
    parsed = urllib.parse.urlparse(nxm_url)
    params = urllib.parse.parse_qs(parsed.query)
    
    key = params.get("key", [""])[0]
    expires = params.get("expires", [""])[0]
    user_id = params.get("user_id", [""])[0]
    
    if not key or not expires or not user_id:
        return None, f"NXM link missing required security tokens: {nxm_url}"
        
    # Query official public API
    api_url = (
        f"{API_BASE_URL}/games/{game_id}/mods/{mod_id}/files/{file_id}/download_link.json"
        f"?key={key}&expires={expires}&user_id={user_id}"
    )
    
    try:
        req = urllib.request.Request(api_url, headers={
            "apikey": api_key,
            "User-Agent": "Vortex/1.11.2",
            "application-name": "Vortex",
            "application-version": "1.11.2"
        })
        with urllib.request.urlopen(req, timeout=10) as api_resp:
            links = json.loads(api_resp.read().decode('utf-8'))
            url = extract_url_from_json(links)
            if url:
                return url, None
            return None, "No CDN link returned in API response"
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8') if e else ""
        return None, f"API HTTP Error ({e.code}): {body or e.reason}"
    except Exception as e:
        return None, f"API Exception: {str(e)}"

def get_nxm_download_url_via_curl(file_id, game_id, mod_id, cookie_header):
    """Try multiple approaches using Chrome-impersonated TLS to bypass Cloudflare."""
    errors = []
    api_key = load_config().get("api_key")

    # 1. Resolve numeric game ID (required by GenerateDownloadUrl and core endpoints)
    numeric_gid = NEXUS_GAME_NUMERIC_IDS.get(str(game_id).lower())
    if not numeric_gid:
        # Fetch the mod page and parse data-game-id attribute from HTML
        mod_url = f"https://www.nexusmods.com/{game_id}/mods/{mod_id}"
        resp = _cffi_get(mod_url, cookie_header, game_id, mod_id, allow_redirects=True)
        if resp is not None and resp.status_code == 200:
            match = re.search(r'data-game-id=["\'](\d+)["\']', resp.text)
            if match:
                try:
                    numeric_gid = int(match.group(1))
                except ValueError:
                    numeric_gid = game_id
            else:
                numeric_gid = game_id
        else:
            numeric_gid = game_id

    # ── Attempt 1 (Primary): Vortex/NMM method (fetch nmm=1 page and call public API)
    # Extremely robust, avoids Cloudflare blocks on POST AJAX requests.
    url, err = get_nxm_download_url_via_nmm(file_id, game_id, mod_id, cookie_header, api_key)
    if url:
        return url, None
    errors.append(f"NMM/Vortex Method: {err}")

    # ── Attempt 2 (Fallback): Use modern GenerateDownloadUrl POST AJAX endpoint (the standard site method)
    ajax_url = "https://www.nexusmods.com/Core/Libs/Common/Managers/Downloads?GenerateDownloadUrl"
    post_data = {
        "fid": str(file_id),
        "game_id": str(numeric_gid)
    }
    resp = _cffi_post(ajax_url, post_data, cookie_header, game_id, mod_id)
    if resp is not None:
        body = resp.text.strip()
        if body and (body.startswith("[") or body.startswith("{")):
            try:
                data = json.loads(body)
                url = data.get("url")
                if url:
                    return url, None
            except Exception:
                pass
        # Try regex scrape as fallback
        url = _extract_download_url_from_html(body, file_id)
        if url:
            return url, None
        errors.append(f"GenerateDownloadUrl: HTTP {resp.status_code}, response={body[:120]}")
    else:
        errors.append("GenerateDownloadUrl request failed (None response)")

    # ── Attempt 3 (Fallback): Scrape the slow-download landing page (file_id in URL)
    slow_page_url = (
        f"https://www.nexusmods.com/{game_id}/mods/{mod_id}"
        f"?tab=files&file_id={file_id}&nmm=0"
    )
    resp = _cffi_get(slow_page_url, cookie_header, game_id, mod_id, allow_redirects=True)
    if resp is not None and resp.status_code == 200:
        url = _extract_download_url_from_html(resp.text, file_id)
        if url:
            return url, None
        errors.append(f"Slow-page scrape: HTTP 200 but no download URL found ({len(resp.text)} bytes)")
    elif resp is not None:
        errors.append(f"Slow-page: HTTP {resp.status_code}")

    # ── Attempt 4 (Fallback): Offline/DownloadLink AJAX — numeric game ID
    ajax_url2 = (
        f"https://www.nexusmods.com/Core/Libs/Mods/Offline/DownloadLink"
        f"?id={file_id}&game_id={numeric_gid}&nmm=0"
    )
    resp = _cffi_get(ajax_url2, cookie_header, game_id, mod_id, allow_redirects=True)
    if resp is not None:
        loc = resp.headers.get("location", "")
        if loc and "nexusmods.com" not in loc and loc.startswith("http"):
            return loc, None
        body = resp.text.strip()
        if body and (body.startswith("[") or body.startswith("{")):
            try:
                data = json.loads(body)
                url = extract_url_from_json(data)
                if url and url.startswith("http"):
                    return url, None
            except Exception:
                pass
        if body.startswith("<"):
            url = _extract_download_url_from_html(body, file_id)
            if url:
                return url, None
        errors.append(f"AJAX(gid={numeric_gid}): HTTP {resp.status_code}, body={body[:120]}")

    return None, " | ".join(errors)

def get_nxm_download_url_via_html(file_id, game_id, mod_id, cookie_header):
    """Fallback: try multiple page URLs and scrape CDN links from HTML."""
    urls_to_try = [
        f"https://www.nexusmods.com/{game_id}/mods/{mod_id}?tab=files&file_id={file_id}&nmm=0",
        f"https://www.nexusmods.com/{game_id}/mods/{mod_id}?tab=files",
    ]
    for page_url in urls_to_try:
        resp = _cffi_get(page_url, cookie_header, game_id, mod_id, allow_redirects=True)
        if resp is None:
            continue
        html = resp.text
        url = _extract_download_url_from_html(html, file_id)
        if url:
            return url, None
        # Also look for nxm:// links which have time-limited API keys
        nxm_match = re.search(
            r'nxm://[^"\'<>\s]+/mods/' + str(mod_id) + r'/files/' + str(file_id) + r'[^"\'<>\s]*',
            html
        )
        if nxm_match:
            return None, f"nxm_link:{nxm_match.group(0)}"
    return None, f"No CDN link found across all page scrape attempts"





# Mapping of popular games to Steam App IDs and Nexus domain names
POPULAR_GAMES = {
    "readyornot": {
        "name": "Ready or Not",
        "steam_appid": "1144200",
        "folder_names": ["Ready Or Not", "readyornot"]
    },
    "skyrimspecialedition": {
        "name": "Skyrim Special Edition",
        "steam_appid": "489830",
        "folder_names": ["Skyrim Special Edition", "skyrimspecialedition"]
    },
    "cyberpunk2077": {
        "name": "Cyberpunk 2077",
        "steam_appid": "1091500",
        "folder_names": ["Cyberpunk 2077", "cyberpunk2077"]
    },
    "fallout4": {
        "name": "Fallout 4",
        "steam_appid": "377160",
        "folder_names": ["Fallout 4", "fallout4"]
    },
    "witcher3": {
        "name": "The Witcher 3: Wild Hunt",
        "steam_appid": "292030",
        "folder_names": ["The Witcher 3", "The Witcher 3 Wild Hunt", "witcher3"]
    },
    "bmxstreets": {
        "name": "Bmx Streets",
        "steam_appid": "1282270",
        "folder_names": ["Bmx Streets", "bmxstreets", "bmx_streets"]
    }
}

# High-fidelity mock mod dataset matching the user's screenshot for "Ready or Not" (readyornot)
MOCK_READY_OR_NOT_MODS = [
    {
        "mod_id": "1001",
        "name": "LVAW and MCX Firing Sound Swap",
        "author": "dixpade",
        "category": "Audio",
        "uploaded_time": "4 hours ago",
        "date_published": "29 May 2026",
        "summary": "Swaps the firing sound between the LVAW and the MCX (Suppressed). MCX without a can will still fire with the original sound.",
        "likes": 1,
        "downloads": 13,
        "size": "83.3MB",
        "thumbnail": "https://cdn.cloudflare.steamstatic.com/steam/apps/1144200/library_600x900.jpg", # Fallback to game art or generic
        "thumbnail_src": "https://staticdelivery.nexusmods.com/mods/3953/images/thumbnails/1001.jpg"
    },
    {
        "mod_id": "1002",
        "name": "LSPD Crye Precision CPC Vests",
        "author": "bravoonecharlie",
        "category": "Outfits",
        "uploaded_time": "18 hours ago",
        "date_published": "28 May 2026",
        "summary": "Adds to the game LSPD SWAT themed Crye CPC plate carriers, converted from the new MLO suspects from A New America.",
        "likes": 2,
        "downloads": 191,
        "size": "17.1MB",
        "thumbnail": "",
        "thumbnail_src": "https://staticdelivery.nexusmods.com/mods/3953/images/thumbnails/1002.jpg"
    },
    {
        "mod_id": "1003",
        "name": "Alternative Intro - edit",
        "author": "SovietMikuu",
        "category": "Visuals",
        "uploaded_time": "22 hours ago",
        "date_published": "28 May 2026",
        "summary": "changes default movie startup for an edit",
        "likes": 0,
        "downloads": 83,
        "size": "4.9MB",
        "thumbnail": "",
        "thumbnail_src": "https://staticdelivery.nexusmods.com/mods/3953/images/thumbnails/1003.jpg"
    },
    {
        "mod_id": "1004",
        "name": "Blue_Archive_Haruka_GA416",
        "author": "OSHaruka",
        "category": "Weapons",
        "uploaded_time": "23 hours ago",
        "date_published": "28 May 2026",
        "summary": "A gun with purple skin about Haruka, Includes gun attachment skin replacers:have EXPS3,558,ODM and M600V. But unfortunately, all gun...",
        "likes": 0,
        "downloads": 65,
        "size": "7.6MB",
        "thumbnail": "",
        "thumbnail_src": "https://staticdelivery.nexusmods.com/mods/3953/images/thumbnails/1004.jpg"
    },
    {
        "mod_id": "1005",
        "name": "COR-45 (MWIII)",
        "author": "neanderthal2",
        "category": "Weapons",
        "uploaded_time": "2 days ago",
        "date_published": "27 May 2026",
        "summary": "A weapon mod that brings the COR-45 from MWIII into Ready or Not.",
        "likes": 4,
        "downloads": 770,
        "size": "10.0MB",
        "thumbnail": "",
        "thumbnail_src": "https://staticdelivery.nexusmods.com/mods/3953/images/thumbnails/1005.jpg"
    },
    {
        "mod_id": "1006",
        "name": "SYKOV (MW2019)",
        "author": "neanderthal2",
        "category": "Weapons",
        "uploaded_time": "2 days ago",
        "date_published": "27 May 2026",
        "summary": "A weapon mod that brings the Sykov from MW2019 into Ready or Not.",
        "likes": 3,
        "downloads": 320,
        "size": "8.5MB",
        "thumbnail": "",
        "thumbnail_src": "https://staticdelivery.nexusmods.com/mods/3953/images/thumbnails/1006.jpg"
    },
    {
        "mod_id": "1007",
        "name": ".357 (MW2019)",
        "author": "neanderthal2",
        "category": "Weapons",
        "uploaded_time": "2 days ago",
        "date_published": "27 May 2026",
        "summary": "A weapon mod that brings the .357 Pistol from MW2019 into Ready or Not.",
        "likes": 2,
        "downloads": 191,
        "size": "12.3MB",
        "thumbnail": "",
        "thumbnail_src": "https://staticdelivery.nexusmods.com/mods/3953/images/thumbnails/1007.jpg"
    },
    {
        "mod_id": "1008",
        "name": "Enhanced Less Lethal Weapon With Extra Long Tactical Version",
        "author": "shirasawa-ui",
        "category": "Weapons",
        "uploaded_time": "2 days ago",
        "date_published": "27 May 2026",
        "summary": "Enhanced non-lethal weapons and added tactical versions of them. Also slightly adjusted the idle weapon holding stance to make it look better.",
        "likes": 10,
        "downloads": 950,
        "size": "1.2MB",
        "thumbnail": "",
        "thumbnail_src": "https://staticdelivery.nexusmods.com/mods/3953/images/thumbnails/1008.jpg"
    },
    {
        "mod_id": "1009",
        "name": "Neon Tomb Music replacement - Robot Rock - Daft Punk",
        "author": "SovietMikuu",
        "category": "Audio",
        "uploaded_time": "2 days ago",
        "date_published": "27 May 2026",
        "summary": "This Mod changes the default boring Neon Tomb Rave Music into something better!",
        "likes": 5,
        "downloads": 120,
        "size": "45.2MB",
        "thumbnail": "",
        "thumbnail_src": "https://staticdelivery.nexusmods.com/mods/3953/images/thumbnails/1009.jpg"
    },
    {
        "mod_id": "1010",
        "name": "DAMSEL'S DISPATCH V5 - OVERTIME EDITION {Boiling Point DLC}",
        "author": "Allison Deere",
        "category": "Audio",
        "uploaded_time": "2 days ago",
        "date_published": "27 May 2026",
        "summary": "The final iteration of Damsel's Dispatch, a realistic TOC replacement mod. Formerly known as \"Femboy TOC\". V5 rework and overhaul for the boiling point DLC.",
        "likes": 8,
        "downloads": 440,
        "size": "18.0MB",
        "thumbnail": "",
        "thumbnail_src": "https://staticdelivery.nexusmods.com/mods/3953/images/thumbnails/1010.jpg"
    }
]

MOCK_GAMES_MODS = {
    "bmxstreets": [
        {
            "mod_id": "3001",
            "name": "Community Map Pack v1.2 - Street & Park Collection",
            "author": "mapder",
            "category": "Maps",
            "uploaded_time": "1 day ago",
            "date_published": "28 May 2026",
            "summary": "A gorgeous collection of 5 community-created street and custom skatepark maps for BMX Streets. Features highly detailed grinding rails, transitions, and massive quarter pipes.",
            "likes": 45,
            "downloads": 820,
            "size": "245.3MB",
            "thumbnail": ""
        },
        {
            "mod_id": "3002",
            "name": "Realistic Physics Rebalance and Weight Adjustment",
            "author": "physguy",
            "category": "Gameplay",
            "uploaded_time": "3 days ago",
            "date_published": "26 May 2026",
            "summary": "Completely rebalances in-game gravity, rider spin speeds, grind frictions, and landing impact forces for a deep, hyper-realistic simulation feel.",
            "likes": 88,
            "downloads": 1420,
            "size": "1.2MB",
            "thumbnail": ""
        },
        {
            "mod_id": "3003",
            "name": "Classic Streetwear Apparel Shoe Pack (Vans & Nike)",
            "author": "streetwear",
            "category": "Outfits",
            "uploaded_time": "4 days ago",
            "date_published": "25 May 2026",
            "summary": "Adds 12 authentic, high-quality classic Vans and Nike shoe models to the character apparel customization deck.",
            "likes": 32,
            "downloads": 540,
            "size": "34.8MB",
            "thumbnail": ""
        },
        {
            "mod_id": "3004",
            "name": "Studio Grinds & Frictional Slide Sound Overhaul",
            "author": "soundmaster",
            "category": "Audio",
            "uploaded_time": "5 days ago",
            "date_published": "24 May 2026",
            "summary": "Replaces grinding noise, sliding, tire screeches, and landing crashes with premium studio-recorded real bicycle audio buffers.",
            "likes": 56,
            "downloads": 990,
            "size": "83.1MB",
            "thumbnail": ""
        },
        {
            "mod_id": "3005",
            "name": "Bmx Streets Script Modding Helper Tool DLL",
            "author": "helper",
            "category": "Scripts",
            "uploaded_time": "6 days ago",
            "date_published": "23 May 2026",
            "summary": "Helper DLL script enabling camera field-of-view overrides, custom replay editor exports, and hud removal options.",
            "likes": 18,
            "downloads": 330,
            "size": "0.4MB",
            "thumbnail": ""
        }
    ],
    "skyrimspecialedition": [
        {
            "mod_id": "4001",
            "name": "SkyUI - Advanced PC-Friendly User Interface",
            "author": "schlangster",
            "category": "User Interface",
            "uploaded_time": "2 days ago",
            "date_published": "27 May 2026",
            "summary": "Elegant, PC-friendly user interface mod for Skyrim Special Edition. Optimizes panel rendering and lists items in clear, sorting-friendly grids.",
            "likes": 2400,
            "downloads": 48200,
            "size": "4.2MB",
            "thumbnail": ""
        },
        {
            "mod_id": "4002",
            "name": "Unofficial Skyrim Special Edition Patch (USSEP)",
            "author": "Arthmoor",
            "category": "Bug Fixes",
            "uploaded_time": "5 days ago",
            "date_published": "24 May 2026",
            "summary": "A comprehensive bug-fixing mod addressing hundreds of gameplay, quest, engine, logic, scripting, text, and placement issues.",
            "likes": 4200,
            "downloads": 95000,
            "size": "135.5MB",
            "thumbnail": ""
        },
        {
            "mod_id": "4003",
            "name": "Alternate Start - Live Another Life",
            "author": "Arthmoor",
            "category": "Gameplay",
            "uploaded_time": "6 days ago",
            "date_published": "23 May 2026",
            "summary": "Skips the long Helgen introduction sequence and lets you choose a custom starting scenario (e.g. ship-wrecked traveler, bandit recruit, tavern patron).",
            "likes": 1900,
            "downloads": 32000,
            "size": "12.4MB",
            "thumbnail": ""
        }
    ],
    "cyberpunk2077": [
        {
            "mod_id": "5001",
            "name": "Cyber Engine Tweaks (CET)",
            "author": "yamashi",
            "category": "Scripts",
            "uploaded_time": "1 day ago",
            "date_published": "28 May 2026",
            "summary": "Core engine scripting framework that fixes performance leaks, binds console commands, and acts as a dependency for advanced gameplay mods.",
            "likes": 3200,
            "downloads": 65000,
            "size": "15.3MB",
            "thumbnail": ""
        },
        {
            "mod_id": "5002",
            "name": "Let There Be Flight - Flying Cars and Bikes Overhaul",
            "author": "jackhumbert",
            "category": "Gameplay",
            "uploaded_time": "3 days ago",
            "date_published": "26 May 2026",
            "summary": "Adds standard flight thruster mechanics to every vehicle in Night City. Features custom flight-assist HUD and dynamic sounds.",
            "likes": 1800,
            "downloads": 24000,
            "size": "45.7MB",
            "thumbnail": ""
        }
    ]
}

# Generate realistic cover art from Steam CDNs
for game_domain, gdata in POPULAR_GAMES.items():
    gdata["cover_url"] = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{gdata['steam_appid']}/library_600x900.jpg"

def scan_for_games():
    """Return manually added games from config file."""
    config = load_config()
    return config.get("games", [])

def get_game_mod_dir(game_domain, exe_path):
    folder_path = os.path.dirname(exe_path)
    if game_domain == "skyrimspecialedition":
        return os.path.join(folder_path, "Data")
    elif game_domain == "fallout4":
        return os.path.join(folder_path, "Data")
    elif game_domain == "readyornot":
        return os.path.join(folder_path, "ReadyOrNot", "Content", "Paks")
    elif game_domain == "witcher3":
        return os.path.join(folder_path, "mods")
    elif game_domain == "cyberpunk2077":
        if os.path.basename(folder_path).lower() == "x64":
            root_path = os.path.abspath(os.path.join(folder_path, "../../"))
        else:
            root_path = folder_path
        return os.path.join(root_path, "archive", "pc", "mod")
    else:
        return os.path.join(folder_path, "mods")

def make_link_or_copy(source, target):
    import shutil
    import ctypes
    
    os.makedirs(os.path.dirname(target), exist_ok=True)
    
    if os.path.exists(target):
        try:
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
        except Exception:
            pass
            
    try:
        success = ctypes.windll.kernel32.CreateHardLinkW(target, source, None)
        if success:
            return True
    except Exception:
        pass
        
    try:
        os.symlink(source, target)
        return True
    except Exception:
        pass
        
    try:
        shutil.copy2(source, target)
        return True
    except Exception:
        return False

def deploy_mod_links(staging_path, target_path):
    deployed_files = []
    for root, dirs, files in os.walk(staging_path):
        for file in files:
            source_file = os.path.join(root, file)
            rel_path = os.path.relpath(source_file, staging_path)
            target_file = os.path.join(target_path, rel_path)
            
            if make_link_or_copy(source_file, target_file):
                deployed_files.append(rel_path)
    return deployed_files

def remove_mod_links(staging_path, target_path):
    for root, dirs, files in os.walk(staging_path):
        for file in files:
            source_file = os.path.join(root, file)
            rel_path = os.path.relpath(source_file, staging_path)
            target_file = os.path.join(target_path, rel_path)
            
            if os.path.exists(target_file):
                try:
                    os.remove(target_file)
                except Exception:
                    pass

def perform_background_download(download_url, output_path, download_id, game_id, file_id, mod_name=""):
    """Download inside a background thread using 4 concurrent chunk streams for maximum download speeds."""
    global downloads_state
    import time
    import ssl
    
    with downloads_lock:
        downloads_state[download_id] = {
            "id": download_id,
            "filename": os.path.basename(output_path),
            "completed_bytes": 0,
            "total_bytes": 0,
            "progress_percent": 0,
            "status": "Downloading",
            "speed": "0 KB/s",
            "game_id": game_id,
            "mod_name": mod_name
        }

    ssl_context = ssl._create_unverified_context()
    
    # 1. Resolve file size and HTTP Range capabilities
    total_bytes = 0
    accept_ranges = False
    try:
        req = urllib.request.Request(
            download_url, 
            headers={"User-Agent": "Vortex/1.11.2"},
            method="HEAD"
        )
        with urllib.request.urlopen(req, context=ssl_context, timeout=5) as resp:
            total_bytes = int(resp.info().get('Content-Length', 0))
            accept_ranges = resp.info().get('Accept-Ranges', '').lower() == 'bytes'
    except Exception:
        try:
            req = urllib.request.Request(download_url, headers={"User-Agent": "Vortex/1.11.2"})
            with urllib.request.urlopen(req, context=ssl_context, timeout=5) as resp:
                total_bytes = int(resp.info().get('Content-Length', 0))
        except Exception:
            pass

    if total_bytes > 0:
        with downloads_lock:
            downloads_state[download_id]["total_bytes"] = total_bytes

    start_time = time.time()
    
    # 2. Multi-threaded download execution (8 parallel connection streams)
    num_threads = 8
    if accept_ranges and total_bytes > 1024 * 1024 * 3:  # Chunking active for files > 3MB
        chunk_size = total_bytes // num_threads
        threads = []
        downloaded_chunks = [0] * num_threads
        
        def download_chunk(thread_idx, start_byte, end_byte):
            temp_chunk_path = f"{output_path}.part{thread_idx}"
            try:
                chunk_req = urllib.request.Request(
                    download_url,
                    headers={
                        "User-Agent": "Vortex/1.11.2",
                        "Range": f"bytes={start_byte}-{end_byte}"
                    }
                )
                with urllib.request.urlopen(chunk_req, context=ssl_context) as resp:
                    with open(temp_chunk_path, "wb") as f:
                        block_size = 1024 * 64
                        while True:
                            buffer = resp.read(block_size)
                            if not buffer:
                                break
                            f.write(buffer)
                            downloaded_chunks[thread_idx] += len(buffer)
                            
                            # Real-time speed and progress calculation
                            total_downloaded = sum(downloaded_chunks)
                            elapsed = time.time() - start_time
                            speed_str = "0 KB/s"
                            if elapsed > 0 and total_downloaded > 0:
                                speed_bps = total_downloaded / elapsed
                                speed_kbps = speed_bps / 1024.0
                                if speed_kbps > 1024:
                                    speed_str = f"{speed_kbps/1024.0:.2f} MB/s"
                                else:
                                    speed_str = f"{speed_kbps:.2f} KB/s"
                                    
                            with downloads_lock:
                                if download_id in downloads_state:
                                    downloads_state[download_id]["completed_bytes"] = total_downloaded
                                    downloads_state[download_id]["speed"] = speed_str
                                    if total_bytes > 0:
                                        downloads_state[download_id]["progress_percent"] = int((total_downloaded / total_bytes) * 100)
            except Exception as e:
                raise e

        try:
            for i in range(num_threads):
                start_byte = i * chunk_size
                end_byte = total_bytes - 1 if i == num_threads - 1 else (i + 1) * chunk_size - 1
                t = threading.Thread(target=download_chunk, args=(i, start_byte, end_byte))
                threads.append(t)
                t.start()
                
            for t in threads:
                t.join()
                
            # Stitch chunk parts together
            with open(output_path, "wb") as outfile:
                for i in range(num_threads):
                    chunk_file = f"{output_path}.part{i}"
                    if os.path.exists(chunk_file):
                        with open(chunk_file, "rb") as infile:
                            outfile.write(infile.read())
                        os.remove(chunk_file)
                        
            with downloads_lock:
                downloads_state[download_id]["status"] = "Completed"
                downloads_state[download_id]["progress_percent"] = 100
        except Exception as e:
            for i in range(num_threads):
                chunk_file = f"{output_path}.part{i}"
                if os.path.exists(chunk_file):
                    try: os.remove(chunk_file)
                    except Exception: pass
            with downloads_lock:
                downloads_state[download_id]["status"] = f"Failed: {str(e)}"
    else:
        # Failsafe standard single stream download
        try:
            req = urllib.request.Request(download_url, headers={"User-Agent": "Vortex/1.11.2"})
            with urllib.request.urlopen(req, context=ssl_context) as response:
                downloaded = 0
                block_size = 1024 * 64
                with open(output_path, 'wb') as f:
                    while True:
                        buffer = response.read(block_size)
                        if not buffer:
                            break
                        f.write(buffer)
                        downloaded += len(buffer)
                        
                        elapsed = time.time() - start_time
                        speed_str = "0 KB/s"
                        if elapsed > 0 and downloaded > 0:
                            speed_bps = downloaded / elapsed
                            speed_kbps = speed_bps / 1024.0
                            if speed_kbps > 1024:
                                speed_str = f"{speed_kbps/1024.0:.2f} MB/s"
                            else:
                                speed_str = f"{speed_kbps:.2f} KB/s"
                                
                        with downloads_lock:
                            if download_id in downloads_state:
                                downloads_state[download_id]["completed_bytes"] = downloaded
                                downloads_state[download_id]["speed"] = speed_str
                                if total_bytes > 0:
                                    downloads_state[download_id]["progress_percent"] = int((downloaded / total_bytes) * 100)
                                    
            with downloads_lock:
                downloads_state[download_id]["status"] = "Completed"
                downloads_state[download_id]["progress_percent"] = 100
        except Exception as e:
            with downloads_lock:
                downloads_state[download_id]["status"] = f"Failed: {str(e)}"
def fetch_collection_details(game_domain, slug, api_key):
    """Fetch collection revisions and mod files using the Nexus Mods v2 GraphQL API."""
    url = "https://api.nexusmods.com/v2/graphql"
    headers = {
        "apikey": api_key,
        "Content-Type": "application/json",
        "User-Agent": "Vortex/1.11.2"
    }
    
    query = """
    query GetCollectionRevision($domainName: String!, $slug: String!) {
      collection(domainName: $domainName, slug: $slug, viewAdultContent: true) {
        name
        summary
        latestPublishedRevision {
          revisionNumber
          modCount
          downloadLink
          modFiles {
            fileId
            gameId
            optional
            file {
              name
              sizeInBytes
              modId
              version
              description
              game {
                domainName
              }
              mod {
                name
              }
            }
          }
        }
      }
    }
    """
    
    payload = {
        "query": query,
        "variables": {
            "domainName": game_domain,
            "slug": slug
        }
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if "errors" in res:
                return None, res["errors"][0].get("message", "GraphQL Query Error")
            
            data = res.get("data", {})
            coll = data.get("collection")
            if not coll:
                return None, "Collection not found in Nexus Mods database."
            
            # Map latestPublishedRevision to revisions list to match existing server.py expectation
            latest = coll.get("latestPublishedRevision")
            if latest:
                coll["revisions"] = [latest]
            else:
                coll["revisions"] = []
                
            return coll, None
    except Exception as e:
        return None, f"GraphQL Request failed: {str(e)}"

class CustomAPIRequestHandler(SimpleHTTPRequestHandler):
    """Custom Request Handler that maps static assets AND implements REST API."""
    
    def end_headers(self):
        # CORS and no-cache enabled for API requests
        if self.path.startswith('/api/'):
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, apikey')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)

        # Static index redirection
        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
            with open(index_path, "rb") as f:
                self.wfile.write(f.read())
            return

        # Favicon and logo image handler
        if path == "/favicon.ico" or path == "/logo.png":
            logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logo.png")
            if not os.path.exists(logo_path):
                logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
            
            if os.path.exists(logo_path):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "max-age=604800")
                self.end_headers()
                with open(logo_path, "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_response(200)
                self.send_header("Content-Type", "image/x-icon")
                self.send_header("Cache-Control", "max-age=604800")
                self.end_headers()
                self.wfile.write(b"")
                return

        # API: Get active downloads progress
        if path == "/api/downloads":
            with downloads_lock:
                status_list = list(downloads_state.values())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(status_list).encode('utf-8'))
            return

        # API: Retrieve configuration
        if path == "/api/settings":
            config = load_config()
            masked_key = ""
            if config.get("api_key"):
                key = config["api_key"]
                masked_key = key[:4] + "*" * (len(key) - 8) + key[-4:] if len(key) > 8 else "****"
            
            masked_sid = ""
            if config.get("sid_cookie"):
                sid = config["sid_cookie"]
                masked_sid = sid[:4] + "*" * (len(sid) - 8) + sid[-4:] if len(sid) > 8 else "****"
            
            response_config = {
                "api_key": masked_key,
                "has_key": bool(config.get("api_key")),
                "sid_cookie": masked_sid,
                "has_sid": bool(config.get("sid_cookie")),
                "custom_paths": config.get("custom_paths", [])
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response_config).encode('utf-8'))
            return

        super().do_GET()

    def do_POST(self):
        path = self.path
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length).decode('utf-8')
        
        try:
            body = json.loads(post_data) if post_data else {}
        except Exception:
            body = {}

        # API: Save Settings
        if path == "/api/settings":
            new_key = body.get("api_key", "").strip()
            new_sid = body.get("sid_cookie", "").strip()
            config = load_config()
            
            if new_key and not new_key.startswith("*") and "*" not in new_key:
                config["api_key"] = new_key
            elif not new_key:
                config["api_key"] = ""
                
            if new_sid and not new_sid.startswith("*") and "*" not in new_sid:
                config["sid_cookie"] = new_sid
            elif not new_sid:
                config["sid_cookie"] = ""
                
            config["custom_paths"] = body.get("custom_paths", [])
            success = save_config(config)
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": success}).encode('utf-8'))
            return

        # API: Choose directory via Native File dialog
        if path == "/api/folder/select":
            if not HAS_TKINTER:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "Native file chooser dialog not supported"}).encode('utf-8'))
                return
                
            selected_path = ""
            def open_dir_dialog():
                nonlocal selected_path
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                selected_path = filedialog.askdirectory(title="Choose Download Folder")
                root.destroy()
                
            open_dir_dialog()
            if not selected_path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "Selection cancelled"}).encode('utf-8'))
                return
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "path": os.path.normpath(selected_path)}).encode('utf-8'))
            return

        # API: Open selected folder in Windows Explorer
        if path == "/api/folder/open":
            folder_path = body.get("folder")
            success = False
            if folder_path and os.path.exists(folder_path):
                try:
                    os.startfile(folder_path)
                    success = True
                except Exception:
                    pass
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": success}).encode('utf-8'))
            return

        # API: Analyze URL
        if path == "/api/analyze":
            url = body.get("url", "").strip()
            if not url:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "Mod URL is required"}).encode('utf-8'))
                return
            
            # Parse URL
            game_id = None
            mod_id = None
            collection_slug = None
            is_collection = False
            
            # Check if it is a collection URL
            # E.g. https://next.nexusmods.com/skyrimspecialedition/collections/rqhcxy
            # E.g. https://www.nexusmods.com/games/cyberpunk2077/collections/rcuccp
            coll_match = re.search(r"nexusmods\.com/(?:games/)?([a-zA-Z0-9_]+)/collections/([a-zA-Z0-9]+)", url)
            if coll_match:
                game_id = coll_match.group(1)
                collection_slug = coll_match.group(2)
                is_collection = True
            else:
                # E.g. https://www.nexusmods.com/readyornot/mods/1001
                mod_match = re.search(r"nexusmods\.com/(?:games/)?([a-zA-Z0-9_]+)/mods/(\d+)", url)
                if mod_match:
                    game_id = mod_match.group(1)
                    mod_id = mod_match.group(2)
            
            if not game_id or (not mod_id and not collection_slug):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "Invalid Nexus Mods URL format"}).encode('utf-8'))
                return
                
            api_key = load_config().get("api_key")
            if not api_key:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "API Key is missing. Please click 'API Settings' in the top-right and paste your Nexus Mods API Key to enable live downloads."}).encode('utf-8'))
                return
                
            if is_collection:
                coll_data, err = fetch_collection_details(game_id, collection_slug, api_key)
                if err:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "error": f"Collection API Error: {err}"}).encode('utf-8'))
                    return
                
                revisions = coll_data.get("revisions", [])
                if not revisions:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "error": "Collection has no published revisions."}).encode('utf-8'))
                    return
                
                # Use latest revision
                latest = revisions[0]
                collection_mods = latest.get("modFiles", [])
                
                files_data = []
                for mc in collection_mods:
                    file_obj = mc.get("file")
                    if not file_obj:
                        continue
                    
                    files_data.append({
                        "file_id": mc.get("fileId"),
                        "name": file_obj.get("name"),
                        "version": file_obj.get("version", "1.0"),
                        "description": file_obj.get("description") or file_obj.get("mod", {}).get("name") or "No description",
                        "size_in_bytes": int(file_obj.get("sizeInBytes") or 0),
                        "mod_id": file_obj.get("modId"),
                        "game_id": file_obj.get("game", {}).get("domainName") or game_id,
                        "mod_name": file_obj.get("mod", {}).get("name") or file_obj.get("name")
                    })
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": True,
                    "is_collection": True,
                    "game_id": game_id,
                    "mod_id": collection_slug,
                    "mod": {
                        "name": coll_data.get("name"),
                        "summary": coll_data.get("summary") or "No description provided.",
                        "version": f"Revision {latest.get('revisionNumber')}",
                        "author": "Nexus Collection",
                        "endorsements": 0,
                        "downloads": 0
                    },
                    "files": files_data
                }).encode('utf-8'))
                return

            mod_data = None
            files_data = []
            
            try:
                # Fetch Mod details
                mod_url = f"{API_BASE_URL}/games/{game_id}/mods/{mod_id}.json"
                req = urllib.request.Request(mod_url, headers={"apikey": api_key, "User-Agent": USER_AGENT})
                with urllib.request.urlopen(req) as resp:
                    mod_data = json.loads(resp.read().decode('utf-8'))
                    
                # Fetch files
                files_url = f"{API_BASE_URL}/games/{game_id}/mods/{mod_id}/files.json"
                req = urllib.request.Request(files_url, headers={"apikey": api_key, "User-Agent": USER_AGENT})
                with urllib.request.urlopen(req) as resp:
                    files_resp = json.loads(resp.read().decode('utf-8'))
                    files_data = files_resp.get("files", [])
            except urllib.error.HTTPError as e:
                err_msg = e.read().decode('utf-8') if e else ""
                try:
                    err_json = json.loads(err_msg)
                    err_msg = err_json.get("message", err_msg)
                except Exception:
                    pass
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": f"Nexus API Error ({e.code}): {err_msg or e.reason}"}).encode('utf-8'))
                return
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": f"Connection Error: {str(e)}"}).encode('utf-8'))
                return
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": True,
                "game_id": game_id,
                "mod_id": mod_id,
                "mod": mod_data,
                "files": files_data
            }).encode('utf-8'))
            return

        # API: Trigger Download to Custom Folder
        if path == "/api/download":
            game_id = body.get("game_id", "readyornot")
            mod_id = body.get("mod_id")
            file_id = body.get("file_id")
            mod_name = body.get("mod_name", f"Mod {mod_id}")
            file_name = body.get("file_name", f"mod_{mod_id}_file_{file_id}.zip")
            download_dir = body.get("download_dir")
            
            if not download_dir:
                download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "NexusMods")
                
            os.makedirs(download_dir, exist_ok=True)
            output_path = os.path.join(download_dir, file_name)
 
            # Generate unique download ID
            download_id = f"{mod_id}_{file_id}"
            
            # Load config
            config = load_config()
            sid_cookie = config.get("sid_cookie", "").strip()
            api_key = config.get("api_key", "").strip()
            
            download_url = None
            cookie_error = None
            api_error = None
            is_free_user = False
            
            # 1. Try session cookie bypass via curl (real TLS fingerprint — passes Cloudflare)
            if sid_cookie:
                cookie_header = sid_cookie
                # If only raw session value was pasted (no key=value pairs), wrap it
                if "=" not in cookie_header:
                    cookie_header = f"nexusmods_session={sid_cookie}"

                # Method A: curl AJAX endpoint (fastest, returns JSON with CDN URL)
                download_url, cookie_error = get_nxm_download_url_via_curl(
                    file_id, game_id, mod_id, cookie_header
                )

                # Method B: scrape CDN link from file page HTML (fallback)
                if not download_url:
                    dl_b, err_b = get_nxm_download_url_via_html(
                        file_id, game_id, mod_id, cookie_header
                    )
                    if dl_b:
                        download_url = dl_b
                        cookie_error = None
                    else:
                        cookie_error = f"Cookie bypass failed. AJAX: {cookie_error} | HTML scrape: {err_b}"


            # 2. Fall back to API Key if cookie failed or is not configured
            if not download_url:
                if api_key:
                    try:
                        url = f"{API_BASE_URL}/games/{game_id}/mods/{mod_id}/files/{file_id}/download_link.json"
                        req = urllib.request.Request(url, headers={
                            "apikey": api_key,
                            "User-Agent": "Vortex/1.11.2",
                            "application-name": "Vortex",
                            "application-version": "1.11.2"
                        })
                        with urllib.request.urlopen(req, timeout=8) as resp:
                            links = json.loads(resp.read().decode('utf-8'))
                            download_url = extract_url_from_json(links)
                    except urllib.error.HTTPError as e:
                        if e.code == 403:
                            is_free_user = True
                            api_error = "Nexus Mods Premium API check failed."
                        else:
                            api_error = f"Nexus API HTTP Error ({e.code}): {e.reason}"
                    except Exception as e:
                        api_error = f"Failed to fetch live download URL from Nexus API: {str(e)}"
                else:
                    if not cookie_error:
                        api_error = "Both Nexus Session Cookie (nexusmods_session) and API Key are missing. Please configure at least one in API Settings."
            
            if not download_url:
                should_trigger_premium_dialog = is_free_user and not sid_cookie
                
                self.send_response(200 if should_trigger_premium_dialog else 400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                
                final_error = cookie_error or api_error or "Could not resolve download URL."
                if sid_cookie and cookie_error:
                    final_error = f"Bypass Method Failed: {cookie_error}"
                
                self.wfile.write(json.dumps({
                    "success": False,
                    "is_free_user": should_trigger_premium_dialog,
                    "error": final_error,
                    "download_page_url": f"https://www.nexusmods.com/{game_id}/mods/{mod_id}?tab=files"
                }).encode('utf-8'))
                return
 
            # Spawn downloader thread
            t = threading.Thread(
                target=perform_background_download,
                args=(download_url, output_path, download_id, game_id, file_id, mod_name),
                daemon=True
            )
            t.start()
 
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": True, 
                "download_id": download_id,
                "save_path": output_path
            }).encode('utf-8'))
            return

        self.send_response(404)
        self.end_headers()

def open_browser():
    import webbrowser
    import time
    time.sleep(0.5)
    webbrowser.open(f"http://localhost:{PORT}")

def main():
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, CustomAPIRequestHandler)
    
    # Automatically launch user browser to UI page
    threading.Thread(target=open_browser, daemon=True).start()
    
    print(f"\n==================================================")
    print(f"  NEXUS MODS RESEARCH WEB DASHBOARD RUNNING")
    print(f"  Access local panel at: http://localhost:{PORT}")
    print(f"==================================================\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        sys.exit(0)

if __name__ == "__main__":
    main()
