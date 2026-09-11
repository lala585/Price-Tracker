"""
Multi-Aggregator RV Inventory Tracker & Price-Drop Engine
=========================================================
Aggregators Included:
- RVTrader.com
- RVUSA.com
- RVT.com
- TrueRVs.com
- RVEnvy.com
- RVUniverse.com
- RVs on Autotrader (rvs.autotrader.com)
- RVPostings.com
- SmartRVGuide.com

Features:
- Polite human jitter delays (15s-25s) and batch resting (60s)
- Automatic SQLite migration with days-on-lot triggers and price history
- View creation for real-time market intelligence
- Automated stale inventory delisting
- Discord Webhook integration for price cuts and new listings
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
BATCH_REST_SECONDS = 30
MIN_QUERY_DELAY = 7.0
MAX_QUERY_DELAY = 15.0

MIN_MODEL_YEAR = 2024
MAX_MODEL_YEAR = 2027

# =============================================================================
# MODELS CATALOG (Standardized Tracking List)
# =============================================================================

MODELS_CATALOG = [
    {"model": "Coachmen Apex Nano 203RBK", "key": "203RBK"},
    {"model": "Coachmen Apex Nano 213RDS", "key": "213RDS"},
    {"model": "Coachmen Apex Nano 224RBS", "key": "224RBS"},
    {"model": "Coachmen Freedom Express Ultra Lite 192RBS", "key": "192RBS"},
    {"model": "Forest River Flagstaff E-Pro E19RL", "key": "E19RL"},
    {"model": "Forest River Flagstaff E-Pro E201RBS", "key": "E201RBS"},
    {"model": "Forest River Flagstaff E-Pro E201SFK", "key": "E201SFK"},
    {"model": "Forest River Flagstaff Micro Lite 21FBRS", "key": "21FBRS"},
    {"model": "Forest River No Boundaries NB19.5", "key": "NB19.5"},
    {"model": "Forest River r-pod RP-205", "key": "RP-205"},
    {"model": "Forest River Rockwood Geo Pro G19FBS", "key": "G19FBS"},
    {"model": "Forest River Rockwood Geo Pro G19RL", "key": "G19RL"},
    {"model": "Forest River Rockwood Geo Pro G19RLS", "key": "G19RLS"},
    {"model": "Forest River Rockwood Geo Pro G20BS", "key": "G20BS"},
    {"model": "Forest River Rockwood Geo Pro G20FK", "key": "G20FK"},
    {"model": "Forest River Rockwood Geo Pro G20RBS", "key": "G20RBS"},
    {"model": "Forest River Rockwood Geo Pro G20SFK", "key": "G20SFK"},
    {"model": "Forest River Rockwood Mini Lite 2109S", "key": "2109S"},
    {"model": "Forest River Surveyor Legend 19RBLE", "key": "19RBLE"},
    {"model": "Forest River Surveyor Legend 202RBLE", "key": "202RBLE"},
    {"model": "Highland Ridge Range Lite Air 16FBS", "key": "16FBS"},
    {"model": "Jayco Jay Feather Air 16FBS", "key": "16FBS"},
    {"model": "Jayco Jay Feather Micro 166FBS", "key": "166FBS"},
    {"model": "Keystone Outback OBX 19RBS", "key": "19RBS"},
    {"model": "Keystone Passport Classic 210RKC", "key": "210RKC"},
    {"model": "Keystone Passport SL 190RD", "key": "190RD"},
    {"model": "Keystone Passport SL 210RK", "key": "210RK"},
    {"model": "Forest River No Boundaries NB19.6", "key": "NB19.6"},
    {"model": "Forest River No Boundaries NB19.4", "key": "NB19.4"},
]


# =============================================================================
# URL BUILDERS
# =============================================================================

def build_rvtrader_url(keyword, zip_code, radius):
    return f"https://www.rvtrader.com/Travel-Trailer/rvs-for-sale?type=Travel%20Trailer%7C198073&keyword={urllib.parse.quote_plus(keyword)}&zip={zip_code}&radius={radius}"

def build_rvusa_url(keyword):
    return f"https://www.rvusa.com/travel-trailer-rvs-for-sale?type=travel+trailer&keyword={urllib.parse.quote_plus(keyword)}"

def build_rvt_url(keyword, zip_code, radius):
    return f"https://www.rvt.com/rvs-for-sale?type=Travel+Trailer&keyword={urllib.parse.quote_plus(keyword)}&zip={zip_code}&distance={radius}"

def build_truervs_url(keyword):
    return f"https://truervs.com/rvs-for-sale?type=travel-trailer&search={urllib.parse.quote_plus(keyword)}"

def build_rvenvy_url(keyword):
    return f"https://rvenvy.com/rvs?category=Travel+Trailer&search={urllib.parse.quote_plus(keyword)}"

def build_rvuniverse_url(keyword):
    return f"https://www.rvuniverse.com/listings/for-sale/travel-trailers/150012?Keywords={urllib.parse.quote_plus(keyword)}"

def build_autotrader_rv_url(keyword, zip_code, radius):
    return f"https://rvs.autotrader.com/rvs-for-sale/travel_trailers-for-sale?zip={zip_code}&distance={radius}&keyword={urllib.parse.quote_plus(keyword)}"

def build_rvpostings_url(keyword):
    return f"https://www.rvpostings.com/rvs-for-sale/search.aspx?type=Travel+Trailer&q={urllib.parse.quote_plus(keyword)}"

def build_smartrvguide_url(keyword):
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", keyword.strip().lower()).strip("-")
    return f"https://www.smartrvguide.com/rvs-for-sale/{slug}"

# =============================================================================
# DATABASE & DATA SYNCHRONIZATION
# =============================================================================

def init_db():
    parent_dir = os.path.dirname(DB_FILE)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
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
            original_price INTEGER,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            status TEXT DEFAULT 'active',
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
            old_price INTEGER,
            new_price INTEGER,
            price_delta INTEGER,
            price INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (listing_id) REFERENCES listings(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("PRAGMA table_info(listings)")
    cols = [r[1] for r in cursor.fetchall()]
    if "original_price" not in cols:
        cursor.execute("ALTER TABLE listings ADD COLUMN original_price INTEGER")
        cursor.execute("UPDATE listings SET original_price = current_price WHERE original_price IS NULL")
    if "status" not in cols:
        cursor.execute("ALTER TABLE listings ADD COLUMN status TEXT DEFAULT 'active'")

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_track_aggregator_price_change
        AFTER UPDATE OF current_price ON listings
        WHEN OLD.current_price != NEW.current_price
        BEGIN
            INSERT INTO price_history (listing_id, source, source_id, old_price, new_price, price_delta, price, recorded_at)
            VALUES (
                OLD.id,
                OLD.source,
                OLD.source_id,
                OLD.current_price,
                NEW.current_price,
                NEW.current_price - OLD.current_price,
                NEW.current_price,
                STRFTIME('%Y-%m-%d %H:%M:%S', 'now')
            );
        END;
    """)

    cursor.execute("""
        CREATE VIEW IF NOT EXISTS v_active_market_intelligence AS
        SELECT 
            id,
            source,
            source_id,
            target_model,
            model_year,
            condition,
            seller_location,
            original_price,
            current_price,
            (COALESCE(original_price, current_price) - current_price) AS total_discount_amount,
            ROUND(((COALESCE(original_price, current_price) - current_price) * 100.0 / NULLIF(original_price, 0)), 1) AS total_discount_pct,
            CAST(julianday('now') - julianday(first_seen) AS INTEGER) AS days_on_lot,
            CAST(julianday('now') - julianday(last_seen) AS INTEGER) AS days_since_last_seen,
            url
        FROM listings
        WHERE status = 'active';
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_listings_source_lookup ON listings(source, source_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_history_lookup ON price_history(source, source_id);")

    conn.commit()
    conn.close()

def sync_unit_to_db(unit):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        "SELECT id, current_price FROM listings WHERE source = ? AND source_id = ?",
        (unit["source"], unit["source_id"])
    )
    row = cursor.fetchone()

    if row is None:
        cursor.execute("""
            INSERT INTO listings (source, source_id, target_model, listing_title, seller_location, 
                                  model_year, condition, current_price, original_price, first_seen, last_seen, status, url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
        """, (
            unit["source"], unit["source_id"], unit["target_model"], unit["listing_title"],
            unit.get("seller_location"), unit.get("model_year"), unit.get("condition"),
            unit["price"], unit["price"], now_str, now_str, unit["url"]
        ))
        conn.commit()
        conn.close()
        return ("NEW", None)

    listing_id, old_price = row
    cursor.execute("""
        UPDATE listings 
        SET current_price = ?, last_seen = ?, status = 'active', listing_title = ?, 
            model_year = ?, condition = ?, seller_location = ?
        WHERE id = ?
    """, (unit["price"], now_str, unit["listing_title"], unit.get("model_year"), unit.get("condition"), unit.get("seller_location"), listing_id))

    conn.commit()
    conn.close()

    if unit["price"] < old_price:
        return ("DROP", old_price)
    elif unit["price"] > old_price:
        return ("INCREASE", old_price)
    return ("SAME", old_price)

def mark_delisted_units(days_threshold=7):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE listings
        SET status = 'delisted'
        WHERE status = 'active'
          AND (julianday('now') - julianday(last_seen)) > ?
    """, (days_threshold,))
    delisted_count = cursor.rowcount
    conn.commit()
    conn.close()
    if delisted_count > 0:
        logging.info(f"Delisted detection: Marked {delisted_count} inactive units as 'delisted'.")

# =============================================================================
# DATA EXTRACTION HELPERS & ALERTS
# =============================================================================

def extract_price(text):
    if not text:
        return None
    scrubbed = re.sub(r"(?:save|savings?|discount|off\s*msrp|down\s*payment|rebate)\s*:?\s*\$\s*([0-9,]+)", "", text, flags=re.I)
    matches = re.findall(r"\$\s?([1-9][0-9]{1,2},[0-9]{3}|[1-9][0-9]{4,5})\b", scrubbed)
    valid = []
    for m in matches:
        cleaned = int(m.replace(",", "").strip())
        if 10000 <= cleaned <= 180000:
            valid.append(cleaned)
    return valid[0] if valid else None

def extract_year(text):
    if not text:
        return None
    match = re.search(r"\b(20[1-3][0-9])\b", text)
    return int(match.group(1)) if match else None

def extract_condition(text):
    if re.search(r"\b(used|pre-?owned)\b", text, re.I):
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
        logging.error(f"Discord alert failed: {e}")

# =============================================================================
# DOM PARSERS
# =============================================================================

def parse_rvtrader_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []
    cards = soup.select("div[class*='listing-card'], div[data-listing-id], article")
    for card in cards:
        text = card.get_text(" ", strip=True)
        if "$" not in text:
            continue
        clean_text = re.sub(r"[^A-Z0-9]", "", text.upper())
        if clean_key not in clean_text:
            continue
        link = card.find("a", href=lambda h: h and "/listing/" in h)
        if not link:
            continue
        url = urljoin("https://www.rvtrader.com", link["href"])
        id_match = re.search(r"listing/.*?([0-9]{7,12})", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(text)
        year = extract_year(text)
        if not price or (year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR)):
            continue

        condition = extract_condition(text)
        title_elem = card.find(["h2", "h3", "h4"])
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"
        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if not any(u["source_id"] == source_id for u in units):
            units.append({
                "source": "RVTrader", "source_id": source_id, "target_model": target_model,
                "listing_title": title, "seller_location": seller_loc, "model_year": year,
                "condition": condition, "price": price, "url": url
            })
    return units

def parse_rvusa_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []
    cards = soup.select("div.listing-container, div[class*='listing-item'], div.result-item, li.listing")
    for card in cards:
        text = card.get_text(" ", strip=True)
        if "$" not in text:
            continue
        clean_text = re.sub(r"[^A-Z0-9]", "", text.upper())
        if clean_key not in clean_text:
            continue
        link = card.find("a", href=True)
        if not link:
            continue
        url = urljoin("https://www.rvusa.com", link["href"])
        id_match = re.search(r"-([0-9]{5,10})(?:\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(text)
        year = extract_year(text)
        if not price or (year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR)):
            continue

        condition = extract_condition(text)
        title_elem = card.find(["h2", "h3", "h4", "a"])
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"
        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if not any(u["source_id"] == source_id for u in units):
            units.append({
                "source": "RVUSA", "source_id": source_id, "target_model": target_model,
                "listing_title": title, "seller_location": seller_loc, "model_year": year,
                "condition": condition, "price": price, "url": url
            })
    return units

def parse_rvt_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []
    cards = soup.select("div[class*='listing-card'], div[class*='srp-item'], div.result-item, article")
    for card in cards:
        text = card.get_text(" ", strip=True)
        if "$" not in text:
            continue
        clean_text = re.sub(r"[^A-Z0-9]", "", text.upper())
        if clean_key not in clean_text:
            continue
        link = card.find("a", href=True)
        if not link:
            continue
        url = urljoin("https://www.rvt.com", link["href"])
        if any(bad in url for bad in ["calc", "price-checker", "printer_page"]):
            continue
        id_match = re.search(r"[-_/]([0-9]{6,10})(?:\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(text)
        year = extract_year(text)
        if not price or (year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR)):
            continue

        condition = extract_condition(text)
        title_elem = card.find(["h2", "h3", "h4", "a"])
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"
        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if not any(u["source_id"] == source_id for u in units):
            units.append({
                "source": "RVT", "source_id": source_id, "target_model": target_model,
                "listing_title": title, "seller_location": seller_loc, "model_year": year,
                "condition": condition, "price": price, "url": url
            })
    return units

def parse_truervs_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []
    cards = soup.select("div[class*='listing-card'], div[class*='vehicle-card'], article, div[data-testid*='listing']")
    for card in cards:
        text = card.get_text(" ", strip=True)
        if "$" not in text:
            continue
        clean_text = re.sub(r"[^A-Z0-9]", "", text.upper())
        if clean_key not in clean_text:
            continue
        link = card.find("a", href=True)
        if not link:
            continue
        url = urljoin("https://truervs.com", link["href"])
        id_match = re.search(r"[-_/]([0-9]{4,10}|[a-f0-9-]{36})(?:\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(text)
        year = extract_year(text)
        if not price or (year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR)):
            continue

        condition = extract_condition(text)
        title_elem = card.find(["h2", "h3", "h4", "a"])
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"
        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if not any(u["source_id"] == source_id for u in units):
            units.append({
                "source": "TrueRVs", "source_id": source_id, "target_model": target_model,
                "listing_title": title, "seller_location": seller_loc, "model_year": year,
                "condition": condition, "price": price, "url": url
            })
    return units

def parse_rvenvy_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []
    cards = soup.select("div[class*='listing-card'], div[class*='vehicle-card'], div.card, article")
    for card in cards:
        text = card.get_text(" ", strip=True)
        if "$" not in text:
            continue
        clean_text = re.sub(r"[^A-Z0-9]", "", text.upper())
        if clean_key not in clean_text:
            continue
        link = card.find("a", href=True)
        if not link:
            continue
        url = urljoin("https://rvenvy.com", link["href"])
        id_match = re.search(r"[-_/]([0-9]{5,10}|[a-f0-9-]{36})(?:\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(text)
        year = extract_year(text)
        if not price or (year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR)):
            continue

        condition = extract_condition(text)
        title_elem = card.find(["h2", "h3", "h4", "a"])
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"
        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if not any(u["source_id"] == source_id for u in units):
            units.append({
                "source": "RVEnvy", "source_id": source_id, "target_model": target_model,
                "listing_title": title, "seller_location": seller_loc, "model_year": year,
                "condition": condition, "price": price, "url": url
            })
    return units

def parse_rvuniverse_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []
    cards = soup.select("div.listing-card, div.listing-item, div.result-item, article")
    for card in cards:
        text = card.get_text(" ", strip=True)
        if "$" not in text:
            continue
        clean_text = re.sub(r"[^A-Z0-9]", "", text.upper())
        if clean_key not in clean_text:
            continue
        link = card.find("a", href=True)
        if not link:
            continue
        url = urljoin("https://www.rvuniverse.com", link["href"])
        id_match = re.search(r"[-_/]([0-9]{6,12})(?:\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(text)
        year = extract_year(text)
        if not price or (year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR)):
            continue

        condition = extract_condition(text)
        title_elem = card.find(["h2", "h3", "h4", "a"])
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"
        loc_match = re.search(r"Location:\s*([A-Za-z\s]+,\s*[A-Z]{2})", text, re.I)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if not any(u["source_id"] == source_id for u in units):
            units.append({
                "source": "RVUniverse", "source_id": source_id, "target_model": target_model,
                "listing_title": title, "seller_location": seller_loc, "model_year": year,
                "condition": condition, "price": price, "url": url
            })
    return units

def parse_autotrader_rv_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []
    cards = soup.select("div[data-cmp='itemCard'], div[class*='listing-card'], article")
    for card in cards:
        text = card.get_text(" ", strip=True)
        if "$" not in text:
            continue
        clean_text = re.sub(r"[^A-Z0-9]", "", text.upper())
        if clean_key not in clean_text:
            continue
        link = card.find("a", href=True)
        if not link:
            continue
        url = urljoin("https://rvs.autotrader.com", link["href"])
        id_match = re.search(r"[-_/]([0-9]{6,10})(?:\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(text)
        year = extract_year(text)
        if not price or (year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR)):
            continue

        condition = extract_condition(text)
        title_elem = card.find(["h2", "h3", "h4", "a"])
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"
        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if not any(u["source_id"] == source_id for u in units):
            units.append({
                "source": "AutotraderRV", "source_id": source_id, "target_model": target_model,
                "listing_title": title, "seller_location": seller_loc, "model_year": year,
                "condition": condition, "price": price, "url": url
            })
    return units

def parse_rvpostings_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []
    cards = soup.select("div.item-block, div.product-box, div[class*='vehicle-item'], article")
    if not cards:
        cards = soup.find_all("div", class_=lambda c: c and any(k in str(c).lower() for k in ["listing", "item"]))

    for card in cards:
        text = card.get_text(" ", strip=True)
        if "$" not in text:
            continue
        clean_text = re.sub(r"[^A-Z0-9]", "", text.upper())
        if clean_key not in clean_text:
            continue
        link = card.find("a", href=True)
        if not link:
            continue
        url = urljoin("https://www.rvpostings.com", link["href"])
        id_match = re.search(r"[-_/]([0-9]{4,10})(?:\.aspx|\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(text)
        year = extract_year(text)
        if not price or (year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR)):
            continue

        condition = extract_condition(text)
        title_elem = card.find(["h2", "h3", "h4", "a"])
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"
        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if not any(u["source_id"] == source_id for u in units):
            units.append({
                "source": "RVPostings", "source_id": source_id, "target_model": target_model,
                "listing_title": title, "seller_location": seller_loc, "model_year": year,
                "condition": condition, "price": price, "url": url
            })
    return units

def parse_smartrvguide_page(html, target_model, clean_key):
    soup = BeautifulSoup(html, "html.parser")
    units = []
    cards = soup.select("div.listing, div.result, div[class*='item-wrap'], li.listing")
    if not cards:
        cards = soup.find_all("div", class_=lambda c: c and "listing" in str(c).lower())

    for card in cards:
        text = card.get_text(" ", strip=True)
        if "$" not in text:
            continue
        clean_text = re.sub(r"[^A-Z0-9]", "", text.upper())
        if clean_key not in clean_text:
            continue
        link = card.find("a", href=True)
        if not link:
            continue
        url = urljoin("https://www.smartrvguide.com", link["href"])
        id_match = re.search(r"[-_/]([0-9]{4,10})(?:\.html|\/|$)", url)
        source_id = id_match.group(1) if id_match else f"URL_{abs(hash(url))}"

        price = extract_price(text)
        year = extract_year(text)
        if not price or (year and (year < MIN_MODEL_YEAR or year > MAX_MODEL_YEAR)):
            continue

        condition = extract_condition(text)
        title_elem = card.find(["h2", "h3", "h4", "a"])
        title = title_elem.get_text(strip=True) if title_elem else f"{year or ''} {target_model}"
        loc_match = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})", text)
        seller_loc = loc_match.group(1).strip() if loc_match else "Regional"

        if not any(u["source_id"] == source_id for u in units):
            units.append({
                "source": "SmartRVGuide", "source_id": source_id, "target_model": target_model,
                "listing_title": title, "seller_location": seller_loc, "model_year": year,
                "condition": condition, "price": price, "url": url
            })
    return units

# =============================================================================
# MAIN CONTROLLER
# =============================================================================

def main():
    init_db()
    logging.info(f"Aggregator initialized: Checking 9 national portals for {len(MODELS_CATALOG)} models.")

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
                    ("RVEnvy", build_rvenvy_url(item["key"]), parse_rvenvy_page),
                    ("RVUniverse", build_rvuniverse_url(item["key"]), parse_rvuniverse_page),
                    ("AutotraderRV", build_autotrader_rv_url(item["key"], SEARCH_ZIP, SEARCH_RADIUS), parse_autotrader_rv_page),
                    ("RVPostings", build_rvpostings_url(item["key"]), parse_rvpostings_page),
                    ("SmartRVGuide", build_smartrvguide_url(item["key"]), parse_smartrvguide_page)
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

    mark_delisted_units(days_threshold=7)

    logging.info(
        f"\nTracker Complete: {total_found} parsed | {new_alerts} new alerts | "
        f"{price_drops} price cuts | Synced to {DB_FILE}"
    )

if __name__ == "__main__":
    main()