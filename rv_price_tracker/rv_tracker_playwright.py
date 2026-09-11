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
- Deterministic SHA-256 fallback IDs for units missing stock numbers
- In-place metadata refreshes on unchanged unit prices
- Human-like randomized jitter delays (3.0s - 6.5s) between individual requests
- Target list shuffling to prevent slamming the same domain sequentially
- Extended batch cooldowns (20s)
- Automatic SQLite schema migrations (model_year, condition)
- Deceptive price scrubbing ("Save $...", deposits, MSRP discounts)
- Accurate New/Used condition classification
- Discord webhook alerts on new inventory and price cuts
"""

import os
import re
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

MIN_MODEL_YEAR = 2024
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
    "Colton RV (PA)": "https://www.coltonrvpa.com/product/travel-trailer?s=true&brand={brand}&keyword={key}",
    "Tom Schaeffers RV (PA)": "https://www.tomschaeffers.com/product/travel-trailers?s=true&brand={brand}&keyword={key}",
    "Petes RV Mid-Atlantic (PA)": "https://www.petesrvmidatlantic.com/rv-search?s=true&lots=1868%2C1815&types=29&brand={brand}&keyword={key}",

    # Ohio (Dealer Spike / NetSource)
    "RCD RV Supercenter (OH)": "https://www.rcdrv.com/rv-search?s=true&brand={brand}&keyword={key}",
    "Meyer's Mentor RV (OH)": "https://www.meyersmentorrv.com/rv-search?s=true&brand={brand}&keyword={key}",
    "Craig Smith RV Center (OH)": "https://www.craigsmithrv.com/rv-search?s=true&brand={brand}&keyword={key}",
    "Greenlawn RV (OH)": "https://www.greenlawnrv.com/rv-search?s=true&brand={brand}&keyword={key}",

    # Michigan (Dealer Spike / NetSource)
    "Veurink's RV (MI)": "https://www.veurinksrv.com/rv-search?s=true&brand={brand}&keyword={key}",
    "Midway RV Center (MI)": "https://www.midwayrv.com/rv-search?s=true&brand={brand}&keyword={key}",

    # Vermont (Dealer Spike / NetSource)
    "Mekkelsen RV (VT)": "https://www.mekkelsenrv.com/rv-search?s=true&types=29&brand={brand}&keyword={key}",
    "Petes RV Vermont (VT)": "https://www.petesrvvt.com/rv-search?s=true&types=29&brand={brand}&keyword={key}",
    "Vermont Outdoors (VT)": "https://www.vermontoutdoorsrv.com/rv-search?s=true&types=29&brand={brand}&keyword={key}",

    # Northern Virginia (Dealer Spike / NetSource)
    "Restless Wheels RV (VA)": "https://www.restlesswheels.com/rv-search?s=true&types=29&brand={brand}&keyword={key}",
}

STEALTH_SUITE_DEALERS = {
    "TerryTown RV (MI)": "https://www.terrytownrv.com/all-inventory/travel-trailers?keyword={key}",
    "Bish's RV (MI)": "https://www.bishs.com/all-inventory/travel-trailers?keyword={key}",
}

# =============================================================================
# TRACKED MODELS CATALOG (27 Lightweight Floorplans)
# =============================================================================

MODELS_CATALOG = [
    {
        "model": "Forest River Flagstaff Micro Lite 21FBRS",
        "key": "21FBRS",
        "std_brand": "flagstaff micro lite",
    },
    {
        "model": "Forest River Rockwood Mini Lite 2109S",
        "key": "2109S",
        "std_brand": "rockwood mini lite",
    },
    {
        "model": "Forest River r-pod RP-205",
        "key": "205",
        "std_brand": "r pod",
    },
    {
        "model": "Forest River Flagstaff E-Pro E201SFK",
        "key": "E201SFK",
        "std_brand": "flagstaff e pro",
    },
    {
        "model": "Forest River Rockwood Geo Pro G20SFK",
        "key": "G20SFK",
        "std_brand": "rockwood geo pro",
    },
    {
        "model": "Forest River No Boundaries NB19.6",
        "key": "19.6",
        "std_brand": "no boundaries",
    },
    {
        "model": "Jayco Jay Feather Micro 166FBS",
        "key": "166FBS",
        "std_brand": "jay feather micro",
    },
    {
        "model": "Jayco Jay Feather Air 16FBS",
        "key": "16FBS",
        "std_brand": "jay feather air",
    },
    {
        "model": "Coachmen Apex Nano 203RBK",
        "key": "203RBK",
        "std_brand": "apex nano",
    },
    {
        "model": "Coachmen Apex Nano 213RDS",
        "key": "213RDS",
        "std_brand": "apex nano",
    },
    {
        "model": "Coachmen Apex Nano 224RBS",
        "key": "224RBS",
        "std_brand": "apex nano",
    },
    {
        "model": "Coachmen Apex Nano 216RKS",
        "key": "216RKS",
        "std_brand": "apex nano",
    },
    {
        "model": "Coachmen Freedom Express Ultra Lite 192RBS",
        "key": "192RBS",
        "std_brand": "freedom express ultra lite",
    },
    {
        "model": "Forest River Surveyor Legend 19RBLE",
        "key": "19RBLE",
        "std_brand": "surveyor legend",
    },
    {
        "model": "Highland Ridge Range Lite Air 16FBS",
        "key": "16FBS",
        "std_brand": "range lite air",
    },
    {
        "model": "Venture RV Sonic 211VRB",
        "key": "211VRB",
        "std_brand": "sonic",
    },
    {
        "model": "Forest River Salem Hemisphere 21RBHL",
        "key": "21RBHL",
        "std_brand": "salem hemisphere",
    },
    {
        "model": "Forest River Wildwood Heritage Glen 21RBHL",
        "key": "21RBHL",
        "std_brand": "wildwood heritage glen",
    },
    {
        "model": "Keystone Passport SL 210RK",
        "key": "210RK",
        "std_brand": "passport sl",
    },
    {
        "model": "Keystone Passport Classic 210RKC",
        "key": "210RKC",
        "std_brand": "passport classic",
    },
    {
        "model": "Grand Design Imagine XLS 19RLE",
        "key": "19RLE",
        "std_brand": "imagine xls",
    },
    {
        "model": "Keystone Passport SL 190RD",
        "key": "190RD",
        "std_brand": "passport sl",
    },
    {
        "model": "Forest River No Boundaries NB19.5",
        "key": "19.5",
        "std_brand": "no boundaries",
    },
    {
        "model": "Forest River r-pod RP-198",
        "key": "198",
        "std_brand": "r-pod",
    },
    {
        "model": "Forest River r-pod RP-207",
        "key": "207",
        "std_brand": "r-pod",
    },
    {
        "model": "Forest River No Boundaries NB18.2",
        "key": "18.2",
        "std_brand": "no boundaries",
    },
    {
        "model": "Forest River Rockwood Geo Pro G19RL",
        "key": "G19RL",
        "std_brand": "rockwood geo pro",
    },
    {
        "model": "Forest River Surveyor Legend 202RBLE",
        "key": "202RBLE",
        "std_brand": "surveyor legend",
    },
    {
        "model": "Forest River Surveyor Legend 203RKLE",
        "key": "203RKLE",
        "std_brand": "surveyor legend",
    },
    {
        "model": "Forest River Rockwood Mini Lite 2205S",
        "key": "2205S",
        "std_brand": "rockwood mini lite",
    },
    {
        "model": "Forest River Flagstaff Micro Lite 22FBS",
        "key": "22FBS",
        "std_brand": "flagstaff micro lite",
    },
    {
        "model": "Keystone Outback OBX 19RBS",
        "key": "19RBS",
        "std_brand": "outback obx",
    },
    {
        "model": "Coachmen Apex Nano 232RBS",
        "key": "232RBS",
        "std_brand": "apex nano",
    },
    {
        "model": "Forest River Cherokee Wolf Pup 16FQ",
        "key": "16FQ",
        "std_brand": "cherokee wolf pup",
    },
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

        # 1. Standard Dealer Spike / NetSource Targets
        for dealer_name, tmpl in DEALER_TEMPLATES.items():
            url = tmpl.format(brand=encoded_brand, key=encoded_key)
            targets.append({
                "dealer": dealer_name,
                "model": item["model"],
                "model_key": item["key"],
                "url": url,
            })

        # 2. Stealth Suite Dealers (TerryTown, Bish's)
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
# DATABASE & DATA SYNCHRONIZATION
# =============================================================================

def init_db():
    """Create tracking and price history tables and auto-migrate missing columns."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
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
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
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
            price INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (listing_id) REFERENCES listings(id)
        )
    """)

    cursor.execute("PRAGMA table_info(listings)")
    columns = [row[1] for row in cursor.fetchall()]

    if "model_year" not in columns:
        logging.info("Migrating schema: Adding 'model_year' column...")
        cursor.execute("ALTER TABLE listings ADD COLUMN model_year INTEGER")
    if "condition" not in columns:
        logging.info("Migrating schema: Adding 'condition' column...")
        cursor.execute("ALTER TABLE listings ADD COLUMN condition TEXT")

    conn.commit()
    conn.close()


def sync_unit_to_db(unit):
    """
    Upsert unit into SQLite database with stable hashing and metadata refreshes.
    """
    stock = unit.get("stock")
    if not stock or stock == "N/A":
        url_key = unit.get("url", "").strip()
        url_hash = hashlib.sha256(url_key.encode("utf-8")).hexdigest()[:12]
        stock = f"URL_{url_hash}"
        unit["stock"] = stock

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    cursor.execute(
        "SELECT id, current_price FROM listings WHERE dealer = ? AND stock = ?",
        (unit["dealer"], unit["stock"])
    )
    row = cursor.fetchone()

    if row is None:
        cursor.execute("""
            INSERT INTO listings (dealer, stock, target_model, listing_title, model_year, condition, current_price, first_seen, last_seen, url, source_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            unit["dealer"], unit["stock"], unit["target_model"], unit["listing_title"],
            unit.get("model_year"), unit.get("condition"), unit["price"], now_str, now_str,
            unit["url"], unit["source_url"]
        ))
        listing_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO price_history (listing_id, dealer, stock, price, recorded_at)
            VALUES (?, ?, ?, ?, ?)
        """, (listing_id, unit["dealer"], unit["stock"], unit["price"], now_str))
        conn.commit()
        conn.close()
        return ("NEW", None)

    listing_id, old_price = row

    if unit["price"] < old_price:
        cursor.execute("""
            UPDATE listings 
            SET current_price = ?, last_seen = ?, listing_title = ?, model_year = ?, condition = ?, url = ?
            WHERE id = ?
        """, (unit["price"], now_str, unit["listing_title"], unit.get("model_year"), unit.get("condition"), unit["url"], listing_id))
        cursor.execute("""
            INSERT INTO price_history (listing_id, dealer, stock, price, recorded_at)
            VALUES (?, ?, ?, ?, ?)
        """, (listing_id, unit["dealer"], unit["stock"], unit["price"], now_str))
        conn.commit()
        conn.close()
        return ("DROP", old_price)

    elif unit["price"] > old_price:
        cursor.execute("""
            UPDATE listings 
            SET current_price = ?, last_seen = ?, listing_title = ?, model_year = ?, condition = ?, url = ?
            WHERE id = ?
        """, (unit["price"], now_str, unit["listing_title"], unit.get("model_year"), unit.get("condition"), unit["url"], listing_id))
        cursor.execute("""
            INSERT INTO price_history (listing_id, dealer, stock, price, recorded_at)
            VALUES (?, ?, ?, ?, ?)
        """, (listing_id, unit["dealer"], unit["stock"], unit["price"], now_str))
        conn.commit()
        conn.close()
        return ("INCREASE", old_price)

    else:
        cursor.execute("""
            UPDATE listings 
            SET last_seen = ?, listing_title = ?, model_year = ?, condition = ?, url = ?
            WHERE id = ?
        """, (now_str, unit["listing_title"], unit.get("model_year"), unit.get("condition"), unit["url"], listing_id))
        conn.commit()
        conn.close()
        return ("SAME", old_price)


# =============================================================================
# DATA CLEANING & EXTRACTION UTILITIES
# =============================================================================

def extract_condition(text):
    """Detect if a unit is New or Used based on text indicators."""
    if not text:
        return "Unknown"
    if re.search(r"\b(pre-?owned|used)\b", text, re.IGNORECASE):
        return "Used"
    if re.search(r"\bnew\b", text, re.IGNORECASE):
        return "New"
    return "Unknown"


def extract_price(text):
    """
    Extract the actual listing/sale price, stripping out misleading
    'Save $...', 'Savings: $...', and deposit callouts.
    """
    if not text:
        return None

    # Strip out explicit savings and down payment figures
    scrubbed_text = re.sub(
        r"(?:save|savings?|discount|off\s*msrp|down\s*payment|rebate)\s*:?\s*\$\s*([0-9,]+)",
        "",
        text,
        flags=re.IGNORECASE
    )

    # 1. High-priority explicit sales label search
    priority_match = re.search(
        r"(?:sale\s*price|our\s*price|internet\s*price|e-price|price)\s*:?\s*\$\s*([1-9][0-9]{1,2},[0-9]{3}|[1-9][0-9]{4,5})\b",
        scrubbed_text,
        re.IGNORECASE
    )
    if priority_match:
        val = int(priority_match.group(1).replace(",", "").strip())
        if 11000 <= val <= 180000:
            return val

    # 2. General dollar amount scan
    matches = re.findall(r"\$\s?([1-9][0-9]{1,2},[0-9]{3}|[1-9][0-9]{4,5})\b", scrubbed_text)
    valid_prices = []
    for m in matches:
        cleaned = int(m.replace(",", "").strip())
        if 11000 <= cleaned <= 180000:
            valid_prices.append(cleaned)

    if not valid_prices:
        return None

    unique_prices = sorted(list(set(valid_prices)))
    return unique_prices[0]


def extract_year(text):
    """Extract 4-digit model year."""
    if not text:
        return None
    match = re.search(r"\b(20[1-3][0-9])\b", text)
    return int(match.group(1)) if match else None


def send_discord_alert(unit, old_price=None):
    """Rich webhook notification to Discord."""
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
# DOM PARSERS
# =============================================================================

def parse_standard_html(html, target):
    """Universal parser for Dealer Spike, NetSource, and Stealth Suite cards."""
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
            "source_url": target["url"]
        })

    logging.info(f"[{target['dealer']}] Verified {len(units)} units matching '{target_key}'.")
    return units


# =============================================================================
# SCRAPING EXECUTION ENGINE
# =============================================================================

def scrape_target(page, target):
    """Visits and parses a given target with isolated state and error resilience."""
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
# MAIN CONTROLLER (BATCHED EXECUTION & POLITE PACING)
# =============================================================================

def main():
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

    logging.info(
        f"\nTracker Run Finished: {total_found} parsed | {new_units} new alerts | "
        f"{price_drops} price cuts | Synced to {DB_FILE}"
    )


if __name__ == "__main__":
    main()