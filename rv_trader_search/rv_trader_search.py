"""
Dual Aggregator RV Inventory Tracker (RV Trader & RVUSA)
========================================================
Tracks 27 lightweight travel trailer floorplans across:
- RVTrader.com (Permissible paths under robots.txt)
- RVUSA.com (NetSource Media catalog)

Features:
- Batched model chunks with polite human-like jitter (3-5s)
- Deterministic ID resolution for both platforms
- SQLite database storage with automatic price history recording
- Discord webhook alerts for newly spotted units and price reductions
"""

import os
import re
import time
import random
import sqlite3
import logging
import urllib.parse
import requests
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DB_FILE = "rv_trader_search/rvtrader_tracker.db" 
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

SEARCH_ZIP = "14218"
SEARCH_RADIUS = "300"

BATCH_SIZE = 4
BATCH_REST_SECONDS = 60
MIN_QUERY_DELAY = 15.0
MAX_QUERY_DELAY = 25.0

MIN_MODEL_YEAR = 2024
MAX_MODEL_YEAR = 2027

# =============================================================================
# MODELS CATALOG
# =============================================================================

MODELS_CATALOG = [
    {"model": "Forest River Flagstaff Micro Lite 21FBRS", "key": "21FBRS"},
    {"model": "Forest River Rockwood Mini Lite 2109S", "key": "2109S"},
    {"model": "Forest River r-pod RP-205", "key": "RP-205"},
    {"model": "Forest River Flagstaff E-Pro E201SFK", "key": "E201SFK"},
    {"model": "Forest River Rockwood Geo Pro G20SFK", "key": "G20SFK"},
    {"model": "Forest River No Boundaries NB19.5", "key": "NB19.5"},
    {"model": "Forest River No Boundaries NB19.6", "key": "NB19.6"},
    {"model": "Jayco Jay Feather Micro 166FBS", "key": "166FBS"},
    {"model": "Jayco Jay Feather Air 16FBS", "key": "16FBS"},
    {"model": "Coachmen Apex Nano 203RBK", "key": "203RBK"},
    {"model": "Coachmen Apex Nano 213RDS", "key": "213RDS"},
    {"model": "Coachmen Apex Nano 224RBS", "key": "224RBS"},
    {"model": "Coachmen Apex Nano 216RKS", "key": "216RKS"},
    {"model": "Coachmen Freedom Express Ultra Lite 192RBS", "key": "192RBS"},
    {"model": "Forest River Surveyor Legend 19RBLE", "key": "19RBLE"},
    {"model": "Highland Ridge Range Lite Air 16FBS", "key": "16FBS"},
    {"model": "Venture RV Sonic 211VRB", "key": "211VRB"},
    {"model": "Forest River Salem Hemisphere 21RBHL", "key": "21RBHL"},
    {"model": "Forest River Wildwood Heritage Glen 21RBHL", "key": "21RBHL"},
    {"model": "Keystone Passport SL 210RK", "key": "210RK"},
    {"model": "Keystone Passport Classic 210RKC", "key": "210RKC"},
    {"model": "Grand Design Imagine XLS 19RLE", "key": "19RLE"},
    {"model": "Keystone Passport SL 190RD", "key": "190RD"},
    {"model": "Forest River r-pod RP-198", "key": "RP-198"},
    {"model": "Forest River r-pod RP-207", "key": "RP-207"},
    {"model": "Forest River No Boundaries NB18.2", "key": "NB18.2"},
    {"model": "Forest River Rockwood Geo Pro G19RL", "key": "G19RL"},
    {"model": "Forest River Surveyor Legend 202RBLE", "key": "202RBLE"},
    {"model": "Forest River Surveyor Legend 203RKLE", "key": "203RKLE"},
    {"model": "Forest River Rockwood Mini Lite 2205S", "key": "2205S"},
    {"model": "Forest River Flagstaff Micro Lite 22FBS", "key": "22FBS"},
    {"model": "Keystone Outback OBX 19RBS", "key": "19RBS"},
    {"model": "Coachmen Apex Nano 232RBS", "key": "232RBS"},
    {"model": "Forest River Cherokee Wolf Pup 16FQ", "key": "16FQ"},
]

# =============================================================================
# URL BUILDERS
# =============================================================================
def build_rvtrader_url(keyword, zip_code, radius):
    base_url = "https://www.rvtrader.com/Travel-Trailer/rvs-for-sale"
    params = {
        "type": "Travel Trailer|198073",
        "keyword": keyword,
        "zip": zip_code,
        "radius": radius
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"
def build_rvuniverse_url(keyword):
    """
    Builds clean RVUniverse travel trailer search URLs avoiding disallowed paths
    (/dealer/, /Compare/, /listinginput/, /ajax*, localized language prefixes, etc.)
    """
    base_url = "https://www.rvuniverse.com/listings/for-sale/travel-trailers/150012"
    params = {
        "Keywords": keyword
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"
def build_rvusa_url(keyword):
    base_url = "https://www.rvusa.com/travel-trailer-rvs-for-sale"
    params = {
        "type": "travel trailer",
        "keyword": keyword
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"

def build_rvt_url(keyword, zip_code, radius):
    """
    Builds clean RVT.com search URLs avoiding disallowed paths
    (calc.*, price-checker, srDisplay, etc.)
    """
    base_url = "https://www.rvt.com/rvs-for-sale"
    params = {
        "type": "Travel Trailer",
        "keyword": keyword,
        "zip": zip_code,
        "distance": radius
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"

def build_rvenvy_url(keyword):
    """
    Builds clean RVEnvy search URLs strictly avoiding disallowed endpoints
    (/api/, /login, /admin, etc.)
    """
    base_url = "https://rvenvy.com/rvs"
    params = {
        "category": "Travel Trailer",
        "search": keyword
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"
def build_truervs_url(keyword):
    """
    Builds clean TrueRVs search URLs strictly avoiding disallowed endpoints
    (/api/, /trpc/, /dashboard/, /sell/, etc.)
    """
    base_url = "https://truervs.com/rvs-for-sale"
    params = {
        "type": "travel-trailer",
        "search": keyword
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"
# =============================================================================
# DATABASE & DATA SYNCHRONIZATION
# =============================================================================
def init_db():
    db_exists = os.path.exists(DB_FILE)
    if db_exists:
        logging.info(f"Database file found at '{DB_FILE}'. Verifying schema...")
    else:
        logging.info(f"Database file not found at '{DB_FILE}'. Creating new database and tables...")
        # Ensure parent folder exists if DB_FILE contains a subdirectory
        parent_dir = os.path.dirname(DB_FILE)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Enable write-ahead logging for improved concurrent durability
    cursor.execute("PRAGMA journal_mode=WAL;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_model TEXT NOT NULL,
            listing_title TEXT NOT NULL,
            seller_location TEXT,
            model_year INTEGER,
            condition TEXT,
            current_price INTEGER NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            url TEXT NOT NULL,
            UNIQUE(source, source_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            price INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (listing_id) REFERENCES listings(id)
        )
    """)

    # Indices to keep duplicate checking fast as history accumulates
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_listings_source_lookup ON listings(source, source_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_history_lookup ON price_history(source, source_id);")

    conn.commit()
    conn.close()

    if not db_exists:
        logging.info(f"Database initialized successfully at '{DB_FILE}'.")
    else:
        logging.info("Database schema verified.")

def sync_unit_to_db(unit):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    cursor.execute(
        "SELECT id, current_price FROM listings WHERE source = ? AND source_id = ?",
        (unit["source"], unit["source_id"])
    )
    row = cursor.fetchone()

    if row is None:
        cursor.execute("""
            INSERT INTO listings (source, source_id, target_model, listing_title, seller_location, model_year, condition, current_price, first_seen, last_seen, url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            unit["source"], unit["source_id"], unit["target_model"], unit["listing_title"],
            unit.get("seller_location"), unit.get("model_year"), unit.get("condition"),
            unit["price"], now_str, now_str, unit["url"]
        ))
        listing_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO price_history (listing_id, source, source_id, price, recorded_at)
            VALUES (?, ?, ?, ?, ?)
        """, (listing_id, unit["source"], unit["source_id"], unit["price"], now_str))
        conn.commit()
        conn.close()
        return ("NEW", None)

    listing_id, old_price = row

    if unit["price"] < old_price:
        cursor.execute("""
            UPDATE listings 
            SET current_price = ?, last_seen = ?, listing_title = ?, model_year = ?, condition = ?, seller_location = ?
            WHERE id = ?
        """, (unit["price"], now_str, unit["listing_title"], unit.get("model_year"), unit.get("condition"), unit.get("seller_location"), listing_id))
        cursor.execute("""
            INSERT INTO price_history (listing_id, source, source_id, price, recorded_at)
            VALUES (?, ?, ?, ?)
        """, (listing_id, unit["source"], unit["source_id"], unit["price"], now_str))
        conn.commit()
        conn.close()
        return ("DROP", old_price)
    else:
        cursor.execute("""
            UPDATE listings 
            SET last_seen = ?, listing_title = ?, model_year = ?, condition = ?, seller_location = ?
            WHERE id = ?
        """, (now_str, unit["listing_title"], unit.get("model_year"), unit.get("condition"), unit.get("seller_location"), listing_id))
        conn.commit()
        conn.close()
        return ("SAME", old_price)

# =============================================================================
# DATA EXTRACTION HELPERS
# =============================================================================

def extract_price(text):
    if not text:
        return None
    matches = re.findall(r"\$\s?([1-9][0-9]{1,2},[0-9]{3}|[1-9][0-9]{4,5})\b", text)
    valid_prices = []
    for m in matches:
        cleaned = int(m.replace(",", "").strip())
        if 10000 <= cleaned <= 180000:
            valid_prices.append(cleaned)
    return valid_prices[0] if valid_prices else None

def extract_year(text):
    if not text:
        return None
    match = re.search(r"\b(20[1-3][0-9])\b", text)
    return int(match.group(1)) if match else None

def extract_condition(text):
    if re.search(r"\bused\b", text, re.I):
        return "Used"
    if re.search(r"\bnew\b", text, re.I):
        return "New"
    return "Unknown"

def send_discord_alert(unit, old_price=None):
    if not DISCORD_WEBHOOK_URL:
        return

    year_str = f" {unit.get('model_year')}" if unit.get("model_year") else ""
    cond_tag = f"[{unit.get('condition')}] " if unit.get("condition") and unit.get("condition") != "Unknown" else ""

    if old_price and unit["price"] < old_price:
        drop = old_price - unit["price"]
        title = f"PRICE DROP: {cond_tag}{unit['target_model']}{year_str}"
        desc = f"**${drop:,} Price Cut!**\nOld: ~~${old_price:,}~~\n**New: ${unit['price']:,}**"
        color = 5763719
    else:
        title = f"Unit Spotted on {unit['source']}: {cond_tag}{unit['target_model']}{year_str}"
        desc = f"**Price: ${unit['price']:,}**"
        color = 3447003

    payload = {
        "username": "RV Aggregator Bot",
        "embeds": [{
            "title": title,
            "url": unit.get("url", ""),
            "description": desc,
            "color": color,
            "fields": [
                {"name": "Source", "value": unit["source"], "inline": True},
                {"name": "Condition", "value": unit.get("condition", "Unknown"), "inline": True},
                {"name": "Location", "value": unit.get("seller_location", "N/A"), "inline": True},
                {"name": "Listing Title", "value": unit.get("listing_title", "N/A"), "inline": False}
            ]
        }]
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        time.sleep(0.5)
    except Exception as e:
        logging.error(f"Discord ping failed: {e}")

# =============================================================================
# DOM PARSERS
# =============================================================================

def parse_rvtrader_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []

    cards = soup.select("div[class*='listing-card'], div[data-listing-id], article")
    for card in cards:
        card_text = card.get_text(" ", strip=True)
        if "$" not in card_text:
            continue

        clean_text = re.sub(r"[^A-Z0-9]", "", card_text.upper())
        if clean_key not in clean_text:
            continue

        link_elem = card.find("a", href=lambda h: h and "/listing/" in h)
        if not link_elem:
            continue

        url = urljoin("https://www.rvtrader.com", link_elem["href"])
        id_match = re.search(r"listing/.*?([0-9]{7,12})", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(card_text)
        if not price:
            continue

        year = extract_year(card_text)
        if year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR):
            continue

        condition = extract_condition(card_text)
        title_elem = card.find(["h2", "h3", "h4"])
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"

        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", card_text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if any(u["source_id"] == source_id for u in units):
            continue

        units.append({
            "source": "RVTrader",
            "source_id": source_id,
            "target_model": target_model,
            "listing_title": title,
            "seller_location": seller_loc,
            "model_year": year,
            "condition": condition,
            "price": price,
            "url": url
        })
    return units

def parse_rvusa_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []

    cards = soup.select("div.listing-container, div[class*='listing-item'], div.result-item, li.listing")
    if not cards:
        cards = soup.find_all("div", class_=lambda c: c and "listing" in c.lower())

    for card in cards:
        card_text = card.get_text(" ", strip=True)
        if "$" not in card_text:
            continue

        clean_text = re.sub(r"[^A-Z0-9]", "", card_text.upper())
        if clean_key not in clean_text:
            continue

        link_elem = card.find("a", href=True)
        if not link_elem:
            continue

        url = urljoin("https://www.rvusa.com", link_elem["href"])

        # RVUSA listing links typically end with /...-id-123456 or a numeric parameter
        id_match = re.search(r"-([0-9]{5,10})(?:\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(card_text)
        if not price:
            continue

        year = extract_year(card_text)
        if year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR):
            continue

        condition = extract_condition(card_text)
        title_elem = card.find(["h2", "h3", "h4", "a"], class_=lambda c: c and any(k in str(c).lower() for k in ["title", "heading"]))
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"

        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", card_text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if any(u["source_id"] == source_id for u in units):
            continue

        units.append({
            "source": "RVUSA",
            "source_id": source_id,
            "target_model": target_model,
            "listing_title": title,
            "seller_location": seller_loc,
            "model_year": year,
            "condition": condition,
            "price": price,
            "url": url
        })
    return units
def parse_rvt_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []

    # RVT listing cards generally use item/result wrappers or article tags
    cards = soup.select("div[class*='listing-card'], div[class*='srp-item'], div.result-item, article")
    if not cards:
        cards = soup.find_all("div", class_=lambda c: c and any(k in str(c).lower() for k in ["listing", "search-result"]))

    for card in cards:
        card_text = card.get_text(" ", strip=True)
        if "$" not in card_text:
            continue

        clean_text = re.sub(r"[^A-Z0-9]", "", card_text.upper())
        if clean_key not in clean_text:
            continue

        link_elem = card.find("a", href=True)
        if not link_elem:
            continue

        url = urljoin("https://www.rvt.com", link_elem["href"])

        # Skip disallowed utility endpoints
        if any(bad in url for bad in ["calc", "price-checker", "printer_page", "srDisplay"]):
            continue

        # RVT listing links typically include an ID number (e.g., /...-id-1234567.html or /item/1234567)
        id_match = re.search(r"[-_/]([0-9]{6,10})(?:\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(card_text)
        if not price:
            continue

        year = extract_year(card_text)
        if year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR):
            continue

        condition = extract_condition(card_text)
        title_elem = card.find(["h2", "h3", "h4", "a"], class_=lambda c: c and any(k in str(c).lower() for k in ["title", "heading"]))
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"

        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", card_text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if any(u["source_id"] == source_id for u in units):
            continue

        units.append({
            "source": "RVT",
            "source_id": source_id,
            "target_model": target_model,
            "listing_title": title,
            "seller_location": seller_loc,
            "model_year": year,
            "condition": condition,
            "price": price,
            "url": url
        })
    return units
def parse_rvenvy_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []

    # RV Envy uses card-based layout elements for vehicle listings
    cards = soup.select("div[class*='listing-card'], div[class*='vehicle-card'], div.card, article")
    if not cards:
        cards = soup.find_all("div", class_=lambda c: c and any(k in str(c).lower() for k in ["listing", "vehicle", "inventory-item"]))

    for card in cards:
        card_text = card.get_text(" ", strip=True)
        if "$" not in card_text:
            continue

        clean_text = re.sub(r"[^A-Z0-9]", "", card_text.upper())
        if clean_key not in clean_text:
            continue

        link_elem = card.find("a", href=True)
        if not link_elem:
            continue

        url = urljoin("https://rvenvy.com", link_elem["href"])

        # robots.txt safety check: skip admin, api, account, or concierge links
        if any(bad in url.lower() for bad in ["/admin", "/api/", "/login", "/account", "/concierge", "/profile"]):
            continue

        # Extract numeric listing ID or slug identifier
        id_match = re.search(r"[-_/]([0-9]{5,10}|[a-f0-9-]{36})(?:\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(card_text)
        if not price:
            continue

        year = extract_year(card_text)
        if year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR):
            continue

        condition = extract_condition(card_text)
        title_elem = card.find(["h2", "h3", "h4", "a"], class_=lambda c: c and any(k in str(c).lower() for k in ["title", "name", "heading"]))
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"

        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", card_text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if any(u["source_id"] == source_id for u in units):
            continue

        units.append({
            "source": "RVEnvy",
            "source_id": source_id,
            "target_model": target_model,
            "listing_title": title,
            "seller_location": seller_loc,
            "model_year": year,
            "condition": condition,
            "price": price,
            "url": url
        })
    return units

def parse_truervs_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []

    # TrueRVs card selectors (standard Next.js / Tailwind card wrappers)
    cards = soup.select("div[class*='listing-card'], div[class*='vehicle-card'], article, div[data-testid*='listing']")
    if not cards:
        cards = soup.find_all("div", class_=lambda c: c and any(k in str(c).lower() for k in ["listing", "rv-card", "vehicle"]))

    for card in cards:
        card_text = card.get_text(" ", strip=True)
        if "$" not in card_text:
            continue

        clean_text = re.sub(r"[^A-Z0-9]", "", card_text.upper())
        if clean_key not in clean_text:
            continue

        link_elem = card.find("a", href=True)
        if not link_elem:
            continue

        url = urljoin("https://truervs.com", link_elem["href"])

        # robots.txt safety filter: skip authentication, apis, and listing funnels
        if any(bad in url.lower() for bad in ["/admin", "/dashboard", "/api", "/trpc", "/login", "/register", "/sell/"]):
            continue

        # Extract numeric listing ID or hash the URL slug
        id_match = re.search(r"[-_/]([0-9]{4,10}|[a-f0-9-]{36})(?:\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(card_text)
        if not price:
            continue

        year = extract_year(card_text)
        if year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR):
            continue

        condition = extract_condition(card_text)
        title_elem = card.find(["h2", "h3", "h4", "a"], class_=lambda c: c and any(k in str(c).lower() for k in ["title", "name", "heading"]))
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"

        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", card_text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if any(u["source_id"] == source_id for u in units):
            continue

        units.append({
            "source": "TrueRVs",
            "source_id": source_id,
            "target_model": target_model,
            "listing_title": title,
            "seller_location": seller_loc,
            "model_year": year,
            "condition": condition,
            "price": price,
            "url": url
        })
    return units
def parse_rvuniverse_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []

    # Sandhills Global listing card selectors
    cards = soup.select("div.listing-card, div.listing-item, div.result-item, div[class*='listing-container'], article")
    if not cards:
        cards = soup.find_all("div", class_=lambda c: c and any(k in str(c).lower() for k in ["listing", "result-item"]))

    for card in cards:
        card_text = card.get_text(" ", strip=True)
        if "$" not in card_text:
            continue

        clean_text = re.sub(r"[^A-Z0-9]", "", card_text.upper())
        if clean_key not in clean_text:
            continue

        link_elem = card.find("a", href=True)
        if not link_elem:
            continue

        url = urljoin("https://www.rvuniverse.com", link_elem["href"])

        # robots.txt safety filter: ignore dealer portals, compare tools, and inputs
        if any(bad in url.lower() for bad in ["/dealer/", "/compare/", "/listinginput/", "/shop/", "/registration/"]):
            continue

        # Extract numeric listing ID from URL (e.g., /listing/for-sale/12345678 or -12345678)
        id_match = re.search(r"[-_/]([0-9]{6,12})(?:\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(card_text)
        if not price:
            continue

        year = extract_year(card_text)
        if year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR):
            continue

        condition = extract_condition(card_text)
        title_elem = card.find(["h2", "h3", "h4", "a"], class_=lambda c: c and any(k in str(c).lower() for k in ["title", "heading", "name"]))
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"

        loc_match = re.search(r"Location:\s*([A-Za-z\s]+,\s*[A-Z]{2})", card_text, re.IGNORECASE)
        if not loc_match:
            loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", card_text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if any(u["source_id"] == source_id for u in units):
            continue

        units.append({
            "source": "RVUniverse",
            "source_id": source_id,
            "target_model": target_model,
            "listing_title": title,
            "seller_location": seller_loc,
            "model_year": year,
            "condition": condition,
            "price": price,
            "url": url
        })
    return units

# =============================================================================
# MAIN CONTROLLER
# =============================================================================

def main():
    init_db()
    logging.info(f"Tracker initialized: checking RV Trader and RVUSA for {len(MODELS_CATALOG)} models.")

    total_found = 0
    new_alerts = 0
    price_drops = 0

    model_chunks = [MODELS_CATALOG[i:i + BATCH_SIZE] for i in range(0, len(MODELS_CATALOG), BATCH_SIZE)]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        for batch_num, chunk in enumerate(model_chunks, 1):
            logging.info(f"\n--- Starting Batch {batch_num}/{len(model_chunks)} ({len(chunk)} models) ---")

            for item in chunk:
                clean_key = re.sub(r"[^A-Z0-9]", "", item["key"].upper())

                targets = [
                    ("RVTrader", build_rvtrader_url(item["key"], SEARCH_ZIP, SEARCH_RADIUS), parse_rvtrader_page),
                    ("RVUSA", build_rvusa_url(item["key"]), parse_rvusa_page),
                    ("RVT", build_rvt_url(item["key"], SEARCH_ZIP, SEARCH_RADIUS), parse_rvt_page),
                    ("TrueRVs", build_truervs_url(item["key"]), parse_truervs_page),
                    ("RVEnvy", build_rvenvy_url(item["key"]), parse_rvenvy_page)
                ]

                for platform_name, url, parse_func in targets:
                    logging.info(f"[{platform_name}] Querying {item['model']}...")

                    try:
                        page.goto(url, timeout=30000, wait_until="domcontentloaded")

                        for btn_text in ["Accept", "Close", "Agree", "I understand", "OK"]:
                            try:
                                btn = page.locator(f"button:has-text('{btn_text}')").first
                                if btn.is_visible(timeout=500):
                                    btn.click()
                            except Exception:
                                pass

                        page.evaluate("window.scrollBy(0, 1000)")
                        page.wait_for_timeout(1500)

                        listings = parse_func(page.content(), item["model"], clean_key)
                        logging.info(f"[{platform_name}] Verified {len(listings)} listings for '{clean_key}'.")

                        for unit in listings:
                            total_found += 1
                            status, old_price = sync_unit_to_db(unit)

                            if status == "NEW":
                                new_alerts += 1
                                send_discord_alert(unit)
                            elif status == "DROP":
                                price_drops += 1
                                send_discord_alert(unit, old_price=old_price)

                    except PlaywrightTimeoutError:
                        logging.warning(f"Timeout on {platform_name} for {item['model']}")
                    except Exception as e:
                        logging.error(f"Error querying {platform_name}: {e}")
                    finally:
                        page.goto("about:blank", timeout=5000, wait_until="commit")

                    delay = random.uniform(MIN_QUERY_DELAY, MAX_QUERY_DELAY)
                    logging.info(f"Pacing: waiting {delay:.2f}s before next query...")
                    time.sleep(delay)

            if batch_num < len(model_chunks):
                logging.info(f"Batch {batch_num} finished. Resting {BATCH_REST_SECONDS}s before next batch...")
                time.sleep(BATCH_REST_SECONDS)

        browser.close()

    logging.info(
        f"\nTracker Complete: {total_found} parsed | {new_alerts} new alerts | "
        f"{price_drops} price cuts | Synced to {DB_FILE}"
    )

if __name__ == "__main__":
    main()
