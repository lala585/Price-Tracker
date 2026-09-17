"""
RV Price & Inventory Tracker (Multi-Dealer Dynamic Matrix)
==========================================================
Automated scraper for dealership inventory across:
- Independent Dealer Spike / NetSource lots (Wilkins, Colton, Seven O's, Meyer's,
  Blue Compass, Tom Schaeffer's, Pete's RV Mid-Atlantic, Family RV, Alpin Haus,
  Oliver's Campers, RV Value Mart, Plattsburgh, Susquehanna, RCD, Greenlawn,
  Craig Smith, Veurink's, Midway, Mekkelsen, Pete's RV VT, Vermont Outdoors, Restless Wheels)
- Coast Technology / Stealth Suite (TerryTown RV, Bish's RV)

Features:
- Batched execution (chunks of 5 models) with isolated browser contexts
- Automated original posted date extraction via JSON-LD, metadata, and CDN image timestamps
- Deterministic SHA-256 fallback IDs for units missing stock numbers
- In-place metadata refreshes on unchanged unit prices
- Human-like randomized jitter delays (5.0s - 10.0s) between individual requests
- Target list shuffling to prevent slamming the same domain sequentially
- Extended batch cooldowns (25s)
- Automatic SQLite schema migrations (model_year, condition, date_posted)
- Deceptive price scrubbing ("Save $...", deposits, MSRP discounts)
- Accurate New/Used condition classification
- Discord webhook alerts on new inventory, price cuts, and posted age
- Script run heartbeat tracking
"""

import os
import re
import json
import time
import random
import sqlite3
import logging
import hashlib
import urllib.parse
import requests
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rv_tracker.db")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

MIN_MODEL_YEAR = 2023
MAX_MODEL_YEAR = 2027

BATCH_SIZE = 5
BATCH_COOLDOWN_SEC = 25
MIN_REQUEST_DELAY = 5.0
MAX_REQUEST_DELAY = 10.0

# =============================================================================
# DEALER & PLATFORM CONFIGURATION
# =============================================================================

DEALER_TEMPLATES = {
    # New York (Dealer Spike / NetSource)
    "Meyer's RV Superstore (NY)": "https://www.meyersrvsuperstores.com/rv-search?s=true&brand={brand}&keyword={key}",
    "Wilkins RV (NY)": "https://www.wilkinsrv.com/rv-search?s=true&brand={brand}&keyword={key}",
    "Blue Compass RV (NY)": "https://www.bluecompassrv.com/product/travel-trailer?s=true&brand={brand}&keyword={key}",
    "Alpin Haus RV (NY)": "https://www.alpinhausrv.com/rv-search?s=true&types=29&brand={brand}&keyword={key}",
    "Colton RV & Marine (NY)": "https://www.coltonrv.com/product/travel-trailer?s=true&brand={brand}&keyword={key}",
    "Plattsburgh RV Store (NY)": "https://www.plattsburghrvstore.com/rv-search?s=true&types=29&brand={brand}&keyword={key}",
    "Seven Os RV (NY)": "https://www.sevenos.com/rv-search?s=true&brand={brand}&keyword={key}",
    "Family RV (NY)": "https://www.familyrvnewark.com/product/travel-trailer?s=true&brand={brand}&keyword={key}",
    "Olivers Campers (NY)": "https://www.oliverscampers.com/product/travel-trailer?s=true&brand={brand}&keyword={key}",

    # Pennsylvania (Dealer Spike / NetSource)
    "Susquehanna RV (PA)": "https://www.susquehannarv.com/rv-search?s=true&brand={brand}&keyword={key}",
    "RV Value Mart (PA)": "https://www.rvvaluemart.com/inventory/type/travel-trailer?search={key}",

    # Ohio (Dealer Spike / NetSource)
    "RCD RV Supercenter (OH)": "https://www.rcdrv.com/rv-search?s=true&brand={brand}&keyword={key}",
    "Craig Smith RV Center (OH)": "https://www.craigsmithrv.com/rv-search?s=true&brand={brand}&keyword={key}",
    "Greenlawn RV (OH)": "https://www.greenlawnrv.com/rv-search?s=true&brand={brand}&keyword={key}",

    # Major Regional Multi-Store Networks
    "General RV Center": "https://www.generalrv.com/rv-search?s=true&types=29&brand={brand}&keyword={key}",
    "Camping World (NY/PA)": "https://rv.campingworld.com/rv-search?category=travel-trailer&keyword={key}",
    
    # High-Volume Pennsylvania Dealerships
    "Ansley RV (PA)": "https://www.ansleyrv.com/rv-search?s=true&brand={brand}&keyword={key}",
}

STEALTH_SUITE_DEALERS = {
    "TerryTown RV (MI)": "https://www.terrytownrv.com/all-inventory/travel-trailers?keyword={key}",
    "Bish's RV (MI)": "https://www.bishs.com/all-inventory/travel-trailers?keyword={key}",
}

# =============================================================================
# TRACKED MODELS CATALOG (Standardized Tracking List)
# =============================================================================

MODELS_CATALOG = [
    {"model": "Gulf Stream Vista Cruiser 17SCD", "key": "17SCD", "std_brand": "vista cruiser"},
    {"model": "Venture RV Sonic Lite SL160VFB", "key": "160VFB", "std_brand": "sonic lite"},
    {"model": "Forest River Rockwood Geo Pro G17SLE", "key": "G17SLE", "std_brand": "geo pro"},
    {"model": "Venture RV Sonic Lite SL150VRK", "key": "150VRK", "std_brand": "sonic lite"},
    {"model": "Forest River Rockwood Geo Pro G19FD", "key": "G19FD", "std_brand": "geo pro"},
    {"model": "Forest River Flagstaff E-Pro E19FD", "key": "E19FD", "std_brand": "e-pro"},
    {"model": "Venture RV Sonic Lite SL167VMS", "key": "167VMS", "std_brand": "sonic lite"},
    {"model": "Forest River Rockwood Geo Pro G19FBS", "key": "G19FBS", "std_brand": "geo pro"},
    {"model": "Forest River Flagstaff E-Pro E19FBS", "key": "E19FBS", "std_brand": "e-pro"},
    {"model": "Forest River Rockwood Geo Pro G19RL", "key": "G19RL", "std_brand": "geo pro"},
    {"model": "Forest River Flagstaff E-Pro E19RL", "key": "E19RL", "std_brand": "e-pro"},
    {"model": "Venture RV Sonic Lite SL169VMK", "key": "169VMK", "std_brand": "sonic lite"},
    {"model": "Forest River Rockwood Geo Pro G17BLE", "key": "G17BLE", "std_brand": "geo pro"},
    {"model": "Venture RV Sonic Lite 169VRK", "key": "169VRK", "std_brand": "sonic lite"},
    {"model": "Gulf Stream Vista Cruiser 19CSK", "key": "19CSK", "std_brand": "vista cruiser"},
    {"model": "Gulf Stream Vista Cruiser 19ERD", "key": "19ERD", "std_brand": "vista cruiser"},
    {"model": "Forest River Ibex 16MBJ", "key": "16MBJ", "std_brand": "ibex"},
    {"model": "Forest River Ibex 16MRJ", "key": "16MRJ", "std_brand": "ibex"},
    {"model": "Coachmen Apex Nano 194BHS", "key": "194BHS", "std_brand": "apex nano"},
    {"model": "Coachmen Apex Nano 187RB", "key": "187RB", "std_brand": "apex nano"},
    {"model": "Coachmen Apex Nano 190RBS", "key": "190RBS", "std_brand": "apex nano"},
    {"model": "Venture RV Sonic Lite SL169VUD", "key": "169VUD", "std_brand": "sonic lite"},
    {"model": "Forest River r-pod RP-192", "key": "RP-192", "std_brand": "r-pod"},
    {"model": "Forest River r-pod RP-180", "key": "RP-180", "std_brand": "r-pod"},
    {"model": "Forest River r-pod RP-190", "key": "RP-190", "std_brand": "r-pod"},
    {"model": "Jayco Jay Feather Air 16RB", "key": "16RB", "std_brand": "jay feather air"},
    {"model": "Highland Ridge Range Lite Air 16FBS", "key": "16FBS", "std_brand": "range lite air"},
    {"model": "Jayco Jay Feather Air 16FBS", "key": "16FBS", "std_brand": "jay feather air"}
]

def chunk_list(lst, n):
    """Yield successive n-sized chunks from a list."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def build_targets_for_models(models):
    """Generates execution targets across all dealer networks for a given list of models."""
    targets = []
    for item in models:
        encoded_brand = urllib.parse.quote_plus(item["std_brand"])
        encoded_key = urllib.parse.quote_plus(item["key"])

        for dealer_name, tmpl in DEALER_TEMPLATES.items():
            url = tmpl.format(brand=encoded_brand, key=encoded_key)
            targets.append({
                "dealer": dealer_name,
                "model": item["model"],
                "model_key": item["key"],
                "url": url,
            })

        for dealer_name, tmpl in STEALTH_SUITE_DEALERS.items():
            url = tmpl.format(key=encoded_key)
            targets.append({
                "dealer": dealer_name,
                "model": item["model"],
                "model_key": item["key"],
                "url": url,
            })
    return targets

# =============================================================================
# DATA CLEANING & EXTRACTION UTILITIES
# =============================================================================

def extract_condition(text):
    if not text:
        return "Unknown"
    if re.search(r"\b(pre-?owned|used)\b", text, re.IGNORECASE):
        return "Used"
    if re.search(r"\bnew\b", text, re.IGNORECASE):
        return "New"
    return "Unknown"

def extract_price(text):
    if not text:
        return None
    scrubbed_text = re.sub(
        r"(?:save|savings?|discount|off\s*msrp|down\s*payment|rebate)\s*:?\s*\$\s*([0-9,]+)",
        "",
        text,
        flags=re.IGNORECASE
    )
    priority_match = re.search(
        r"(?:sale\s*price|our\s*price|internet\s*price|e-price|price)\s*:?\s*\$\s*([1-9][0-9]{1,2},[0-9]{3}|[1-9][0-9]{4,5})\b",
        scrubbed_text,
        re.IGNORECASE
    )
    if priority_match:
        val = int(priority_match.group(1).replace(",", "").strip())
        if 11000 <= val <= 180000:
            return val

    matches = re.findall(r"\$\s?([1-9][0-9]{1,2},[0-9]{3}|[1-9][0-9]{4,5})\b", scrubbed_text)
    valid_prices = []
    for m in matches:
        cleaned = int(m.replace(",", "").strip())
        if 11000 <= cleaned <= 180000:
            valid_prices.append(cleaned)

    if not valid_prices:
        return None
    return sorted(list(set(valid_prices)))[0]

def extract_year(text):
    if not text:
        return None
    match = re.search(r"\b(20[1-3][0-9])\b", text)
    return int(match.group(1)) if match else None

def extract_posted_date(html_or_soup):
    """Extracts original listing date via JSON-LD, meta tags, or CDN image path timestamps."""
    soup = BeautifulSoup(html_or_soup, "html.parser") if isinstance(html_or_soup, str) else html_or_soup

    # 1. Inspect application/ld+json schemas
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            if not script.string:
                continue
            data = json.loads(script.string)
            stack: list[dict | list] = [data] if isinstance(data, dict) else list(data)
            while stack:
                curr = stack.pop()
                if isinstance(curr, dict):
                    for key in ["datePosted", "dateCreated", "releaseDate", "validFrom", "uploadDate"]:
                        if key in curr and curr[key]:
                            date_str = str(curr[key])
                            match = re.search(r"(\d{4}-\d{2}-\d{2})", date_str)
                            if match:
                                return match.group(1)
                    for value in curr.values():
                        if isinstance(value, (dict, list)):
                            stack.append(value)
                elif isinstance(curr, list):
                    stack.extend(curr)
        except Exception:
            continue

    # 2. Inspect OpenGraph and HTML meta tags
    for meta_name in ["article:published_time", "og:published_time", "date", "pubdate", "publishdate"]:
        meta = soup.find("meta", attrs={"property": meta_name}) or soup.find("meta", attrs={"name": meta_name})
        content = str(meta.get("content") or "") if meta else ""
        if content:
            match = re.search(r"(\d{4}-\d{2}-\d{2})", content)
            if match:
                return match.group(1)

    # 3. Fallback: Parse CDN image upload timestamps in <img> tags
    for img in soup.find_all("img", src=True):
        src = str(img.get("src") or "")
        path_match = re.search(r"/(202[4-7])/([0-1][0-9])/([0-3][0-9])/", src)
        if path_match:
            y, m, d = path_match.groups()
            return f"{y}-{m}-{d}"
        stamp_match = re.search(r"[_\/-](202[4-7])([0-1][0-9])([0-3][0-9])[_\/-]", src)
        if stamp_match:
            y, m, d = stamp_match.groups()
            return f"{y}-{m}-{d}"

    return None

def send_discord_alert(unit, old_price=None):
    if not DISCORD_WEBHOOK_URL:
        return

    year_str = f" {unit.get('model_year')}" if unit.get("model_year") else ""
    cond_tag = f"[{unit.get('condition')}] " if unit.get("condition") and unit.get("condition") != "Unknown" else ""
    date_tag = unit.get("date_posted") or "Unknown"

    if old_price and unit["price"] < old_price:
        drop = old_price - unit["price"]
        title = f"PRICE DROP: {cond_tag}{unit['target_model']}{year_str}"
        desc = f"**${drop:,} Price Cut!**\nOld: ~~${old_price:,}~~\n**New: ${unit['price']:,}**"
        color = 5763719
    else:
        title = f"Unit Spotted: {cond_tag}{unit['target_model']}{year_str}"
        desc = f"**Price: ${unit['price']:,}**"
        color = 3447003

    payload = {
        "username": "RV Lot Bot",
        "embeds": [{
            "title": title,
            "url": unit.get("url", ""),
            "description": desc,
            "color": color,
            "fields": [
                {"name": "Dealership", "value": unit["dealer"], "inline": True},
                {"name": "Condition", "value": unit.get("condition", "Unknown"), "inline": True},
                {"name": "Date Posted", "value": date_tag, "inline": True},
                {"name": "Stock #", "value": str(unit.get("stock", "N/A")), "inline": True},
                {"name": "Year", "value": str(unit.get("model_year") or "N/A"), "inline": True},
                {"name": "Listing Title", "value": unit.get("listing_title", "N/A"), "inline": False}
            ],
            "footer": {"text": "Regional Inventory Tracker (NY/PA/OH/MI/VT/VA)"}
        }]
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        time.sleep(0.5)
        if resp.status_code not in (200, 204):
            logging.warning(f"Discord webhook error {resp.status_code}: {resp.text}")
    except Exception as e:
        logging.error(f"Discord ping failed: {e}")

# =============================================================================
# DATABASE & DATA SYNCHRONIZATION
# =============================================================================

def log_run_start(db_path: str = DB_FILE) -> int:
    parent_dir = os.path.dirname(db_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS script_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp TEXT NOT NULL,
            status TEXT NOT NULL,
            records_scraped INTEGER DEFAULT 0
        );
    """)
    now_str = datetime.now().isoformat()
    cursor.execute("INSERT INTO script_runs (run_timestamp, status) VALUES (?, ?);", (now_str, "IN_PROGRESS"))
    conn.commit()
    run_id = cursor.lastrowid
    conn.close()
    if run_id is None:
        raise RuntimeError("Unable to create a new database run record.")
    run_id = int(run_id)
    print(f"[DB Write OK] Run #{run_id} initiated at {now_str}.")
    return run_id

def log_run_finish(run_id: int, records_count: int, db_path: str = DB_FILE):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE script_runs
        SET status = 'COMPLETED',
            records_scraped = ?
        WHERE run_id = ?;
    """, (records_count, run_id))
    conn.commit()
    conn.close()
    print(f"[DB Run Completed] Run #{run_id} logged {records_count} records.")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dealer TEXT NOT NULL,
            stock TEXT NOT NULL,
            target_model TEXT NOT NULL,
            listing_title TEXT NOT NULL,
            model_year INTEGER,
            condition TEXT,
            current_price INTEGER NOT NULL,
            original_price INTEGER,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            date_posted TEXT,
            status TEXT DEFAULT 'active',
            url TEXT,
            source_url TEXT,
            UNIQUE(dealer, stock)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            dealer TEXT NOT NULL,
            stock TEXT NOT NULL,
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
    if "model_year" not in cols:
        cursor.execute("ALTER TABLE listings ADD COLUMN model_year INTEGER")
    if "condition" not in cols:
        cursor.execute("ALTER TABLE listings ADD COLUMN condition TEXT")
    if "date_posted" not in cols:
        cursor.execute("ALTER TABLE listings ADD COLUMN date_posted TEXT")

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_track_dealer_price_change
        AFTER UPDATE OF current_price ON listings
        WHEN OLD.current_price != NEW.current_price
        BEGIN
            INSERT INTO price_history (listing_id, dealer, stock, old_price, new_price, price_delta, price, recorded_at)
            VALUES (
                OLD.id,
                OLD.dealer,
                OLD.stock,
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
            dealer,
            stock,
            target_model,
            model_year,
            condition,
            original_price,
            current_price,
            date_posted,
            (COALESCE(original_price, current_price) - current_price) AS total_discount_amount,
            ROUND(((COALESCE(original_price, current_price) - current_price) * 100.0 / NULLIF(original_price, 0)), 1) AS total_discount_pct,
            CAST(julianday('now') - julianday(first_seen) AS INTEGER) AS days_on_lot,
            CAST(julianday('now') - julianday(last_seen) AS INTEGER) AS days_since_last_seen,
            url
        FROM listings
        WHERE status = 'active';
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dealer_listings_lookup ON listings(dealer, stock);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dealer_price_hist ON price_history(listing_id);")

    conn.commit()
    conn.close()

def sync_unit_to_db(unit):
    stock = unit.get("stock")
    if not stock or stock == "N/A":
        url_key = unit.get("url", "").strip()
        url_hash = hashlib.sha256(url_key.encode("utf-8")).hexdigest()[:12]
        stock = f"URL_{url_hash}"
        unit["stock"] = stock

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        "SELECT id, current_price FROM listings WHERE dealer = ? AND stock = ?",
        (unit["dealer"], unit["stock"])
    )
    row = cursor.fetchone()

    if row is None:
        cursor.execute("""
            INSERT INTO listings (dealer, stock, target_model, listing_title, model_year, condition, 
                                  current_price, original_price, first_seen, last_seen, date_posted, 
                                  status, url, source_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """, (
            unit["dealer"], unit["stock"], unit["target_model"], unit["listing_title"],
            unit.get("model_year"), unit.get("condition"), unit["price"], unit["price"],
            now_str, now_str, unit.get("date_posted"), unit["url"], unit["source_url"]
        ))
        conn.commit()
        conn.close()
        return ("NEW", None)

    listing_id, old_price = row
    cursor.execute("""
        UPDATE listings 
        SET current_price = ?, last_seen = ?, status = 'active', listing_title = ?, 
            model_year = ?, condition = ?, url = ?,
            date_posted = COALESCE(date_posted, ?)
        WHERE id = ?
    """, (
        unit["price"], now_str, unit["listing_title"], unit.get("model_year"),
        unit.get("condition"), unit["url"], unit.get("date_posted"), listing_id
    ))
    
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
# DOM PARSER
# =============================================================================

def parse_standard_html(html, target):
    soup = BeautifulSoup(html, "html.parser")
    units = []

    card_selectors = [
        "div.product-item",
        "div.unit-item",
        "div[class*='product-card']",
        "div[class*='unit-card']",
        "li.unit",
        "li[class*='unit-']",
        "div.v7list-item",
        "div[data-unit-id]",
        "div.inventory-item",
        "li.inventory-item",
        "div.unit-tile",
        "div.vehicle-card",
        "div[data-vehicle-id]",
        "article"
    ]

    cards = []
    seen = set()
    for selector in card_selectors:
        for el in soup.select(selector):
            if id(el) not in seen and not any(id(p) in seen for p in el.parents):
                seen.add(id(el))
                cards.append(el)

    target_key = target.get("model_key", "").upper()

    for card in cards:
        card_text = card.get_text(" ", strip=True)
        if "$" not in card_text:
            continue

        title_elem = card.find(["h2", "h3", "h4", "a"], class_=lambda c: c and any(k in str(c).lower() for k in ["title", "name", "heading"]))
        raw_title = title_elem.get_text(strip=True) if title_elem else card_text[:120]
        title = raw_title.split("Stock")[0].split("VIN")[0].strip()

        combined_identity = f"{title} {card_text}".upper()
        clean_key = re.sub(r"[^A-Z0-9]", "", target_key)
        clean_identity = re.sub(r"[^A-Z0-9]", "", combined_identity)

        if clean_key and clean_key not in clean_identity:
            continue

        unit_year = extract_year(title) or extract_year(card_text)
        if unit_year and (unit_year < MIN_MODEL_YEAR or unit_year > MAX_MODEL_YEAR):
            continue

        price = extract_price(card_text)
        if not price:
            continue

        condition = extract_condition(title)
        if condition == "Unknown":
            condition = extract_condition(card_text)

        stock_match = re.search(r"(?:Stock|STK|VIN)\s*#?:?\s*([A-Za-z0-9\-]+)", card_text, re.IGNORECASE)
        stock = stock_match.group(1) if stock_match else "N/A"

        link_elem = card.find("a", href=True)
        link = urljoin(target["url"], link_elem["href"]) if link_elem else target["url"]
        posted_date = extract_posted_date(card)

        if any(u["url"] == link and u["stock"] == stock for u in units):
            continue

        units.append({
            "dealer": target["dealer"],
            "target_model": target["model"],
            "listing_title": title,
            "model_year": unit_year,
            "condition": condition,
            "price": price,
            "stock": stock,
            "url": link,
            "source_url": target["url"],
            "date_posted": posted_date
        })

    logging.info(f"[{target['dealer']}] Verified {len(units)} units matching '{target_key}'.")
    return units

# =============================================================================
# SCRAPING EXECUTION ENGINE
# =============================================================================

def scrape_target(page, target):
    logging.info(f"Visiting {target['dealer']} -> {target['model']}...")
    try:
        response = page.goto(target["url"], timeout=30000, wait_until="domcontentloaded")
        
        if response and response.status >= 400:
            logging.warning(f"HTTP {response.status} on {target['url']}")
            return []

        for btn_text in ["Accept", "Close", "Agree", "Continue", "OK"]:
            try:
                btn = page.locator(f"button:has-text('{btn_text}')").first
                if btn.is_visible(timeout=800):
                    btn.click()
            except Exception:
                pass

        card_selectors = (
            "div.product-item, div.unit-item, div[class*='product-card'], "
            "div[class*='unit-card'], li.unit, li[class*='unit-'], "
            "div.v7list-item, div[data-unit-id], div.inventory-item, "
            "li.inventory-item, div.unit-tile, div.vehicle-card, "
            "div[data-vehicle-id], article"
        )
        try:
            page.wait_for_selector(card_selectors, timeout=8000, state="attached")
        except PlaywrightTimeoutError:
            pass

        page.evaluate("window.scrollBy(0, document.body.scrollHeight / 3)")
        page.wait_for_timeout(1000)
        page.evaluate("window.scrollBy(0, document.body.scrollHeight / 3)")

        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except PlaywrightTimeoutError:
            pass

        html_content = page.content()
        return parse_standard_html(html_content, target)

    except PlaywrightTimeoutError:
        logging.warning(f"Timeout navigating to {target['url']}")
        return []
    except Exception as e:
        logging.error(f"Scrape error on {target['dealer']}: {e}")
        return []
    finally:
        try:
            page.goto("about:blank", timeout=5000, wait_until="commit")
        except Exception:
            pass

# =============================================================================
# MAIN CONTROLLER
# =============================================================================

def main():
    try:
        current_run_id = log_run_start(DB_FILE)
    except Exception as e:
        print(f"CRITICAL: Database is not writable! Error: {e}")
        exit(1)
    init_db()

    model_batches = list(chunk_list(MODELS_CATALOG, BATCH_SIZE))
    total_models = len(MODELS_CATALOG)
    logging.info(
        f"Tracker initialized: {total_models} models divided into "
        f"{len(model_batches)} batches (Batch Size: {BATCH_SIZE})."
    )

    total_found = 0
    new_units = 0
    price_drops = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for idx, batch in enumerate(model_batches, start=1):
            logging.info(
                f"\n>>> Starting Batch {idx}/{len(model_batches)} "
                f"({len(batch)} models: {', '.join(m['key'] for m in batch)}) <<<"
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()

            targets = build_targets_for_models(batch)
            random.shuffle(targets)
            logging.info(f"Generated and shuffled {len(targets)} execution targets for Batch {idx}.")

            for target in targets:
                listings = scrape_target(page, target)

                for unit in listings:
                    total_found += 1
                    status, old_price = sync_unit_to_db(unit)

                    if status == "NEW":
                        new_units += 1
                        send_discord_alert(unit)
                    elif status == "DROP":
                        price_drops += 1
                        send_discord_alert(unit, old_price=old_price)

                delay = random.uniform(MIN_REQUEST_DELAY, MAX_REQUEST_DELAY)
                logging.info(f"Pacing: sleeping {delay:.2f}s before next query...")
                time.sleep(delay)

            context.close()

            if idx < len(model_batches):
                logging.info(f"Cooldown pause: waiting {BATCH_COOLDOWN_SEC}s before starting Batch {idx + 1}...")
                time.sleep(BATCH_COOLDOWN_SEC)

        browser.close()

    mark_delisted_units(days_threshold=7)
    logging.info(
        f"\nTracker Run Finished: {total_found} parsed | {new_units} new alerts | "
        f"{price_drops} price cuts | Synced to {DB_FILE}"
    )
    log_run_finish(current_run_id, total_found, DB_FILE)

if __name__ == "__main__":
    main()
