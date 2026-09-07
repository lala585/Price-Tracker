"""
RV Price & Inventory Tracker (Playwright + DOM Parser)
======================================================
Automated scraper for dealership inventory across Wilkins RV,
Meyer's RV Superstores, Colton RV, Seven O's RV, and Camping World.
"""

import re
import csv
import logging
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import os
import json 


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

LOG_FILE = "rv_price_history.csv"
CSV_FIELDS = [
    "timestamp", "dealer", "target_model", "listing_title",
    "price", "msrp", "stock", "availability", "url", "source_url"
]

# Cleaned search URLs with relaxed keyword queries to maximize matches
TARGET_SEARCHES = [
    # Coachmen RV Apex Nano 190RBS
    {"dealer": "Wilkins RV", "model": "Coachmen RV Apex Nano 190RBS", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=190RBS"},
    {"dealer": "Meyer's RV", "model": "Coachmen RV Apex Nano 190RBS", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=190RBS"},
    {"dealer": "Colton RV", "model": "Coachmen RV Apex Nano 190RBS", "url": "https://www.coltonrv.com/rv-search?s=true&keyword=190RBS"},
    {"dealer": "Seven Os RV", "model": "Coachmen RV Apex Nano 190RBS", "url": "https://www.sevenos.com/rv-search?s=true&keyword=190RBS"},

    # Coachmen RV Freedom Express Select 19SE
    {"dealer": "Wilkins RV", "model": "Coachmen RV Freedom Express Select 19SE", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=19SE"},
    {"dealer": "Meyer's RV", "model": "Coachmen RV Freedom Express Select 19SE", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=19SE"},
    {"dealer": "Colton RV", "model": "Coachmen RV Freedom Express Select 19SE", "url": "https://www.coltonrv.com/rv-search?s=true&keyword=19SE"},
    {"dealer": "Seven Os RV", "model": "Coachmen RV Freedom Express Select 19SE", "url": "https://www.sevenos.com/rv-search?s=true&keyword=19SE"},

    # Forest River Ibex 16MBJ-BM
    {"dealer": "Wilkins RV", "model": "Forest River Ibex 16MBJ-BM", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=16MBJ"},
    {"dealer": "Meyer's RV", "model": "Forest River Ibex 16MBJ-BM", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=16MBJ"},
    {"dealer": "Colton RV", "model": "Forest River Ibex 16MBJ-BM", "url": "https://www.coltonrv.com/rv-search?s=true&keyword=16MBJ"},

    # Forest River No Boundaries NB18.2-BM
    {"dealer": "Wilkins RV", "model": "Forest River No Boundaries NB18.2-BM", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=NB18.2"},
    {"dealer": "Meyer's RV", "model": "Forest River No Boundaries NB18.2-BM", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=NB18.2"},

    # East To West Longitude 185RB
    {"dealer": "Wilkins RV", "model": "East To West Longitude 185RB", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=185RB"},
    {"dealer": "Meyer's RV", "model": "East To West Longitude 185RB", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=185RB"},

    # Forest River Surveyor Legend 19RBLE
    {"dealer": "Wilkins RV", "model": "Forest River Surveyor Legend 19RBLE", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=19RBLE"},
    {"dealer": "Meyer's RV", "model": "Forest River Surveyor Legend 19RBLE", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=19RBLE"},

    # Forest River Rockwood Mini Lite 2109S
    {"dealer": "Colton RV", "model": "Forest River Rockwood Mini Lite 2109S", "url": "https://www.coltonrv.com/rv-search?s=true&keyword=2109S"},
    {"dealer": "Camping World", "model": "Forest River Rockwood Mini Lite 2109S", "url": "https://rv.campingworld.com/shop-rvs?query=2109s"},
]

def clean_price(price_str):
    """Extract numeric value from currency strings."""
    if not price_str:
        return None
    cleaned = "".join(c for c in str(price_str) if c.isdigit())
    return int(cleaned) if cleaned else None

def send_discord_alert(unit, old_price=None):
    """Optional webhook notification to Discord channel."""
    if not DISCORD_WEBHOOK_URL:
        return

    import requests
    if old_price and unit["price"] < old_price:
        drop = old_price - unit["price"]
        title = f"PRICE DROP: {unit['target_model']}"
        desc = f"**${drop:,} Price Drop!**\nOld: ~~${old_price:,}~~\n**New: ${unit['price']:,}**"
        color = 5763719
    else:
        title = f"Unit Spotted: {unit['target_model']}"
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
                {"name": "Stock #", "value": str(unit.get("stock", "N/A")), "inline": True},
                {"name": "Listing Title", "value": unit.get("listing_title", "N/A"), "inline": False}
            ]
        }]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Discord ping failed: {e}")

def parse_rendered_html(html, target):
    """Extract listing information from the rendered DOM."""
    soup = BeautifulSoup(html, "html.parser")
    units = []

    # Broad selector coverage matching modern Dealer Spike, RVT, and NetSource grids
    card_selectors = [
        "div[class*='unit-item']",
        "div[class*='vehicle-card']",
        "div[class*='inventory-item']",
        "li[class*='inventory-item']",
        "div[class*='unit-tile']",
        "div[class*='search-result-unit']",
        "div[class*='listing-item']",
        "article"
    ]

    cards = []
    for selector in card_selectors:
        found = soup.select(selector)
        if len(found) > len(cards):
            cards = found

    for card in cards:
        card_text = card.get_text(" ", strip=True)
        if "$" not in card_text:
            continue

        # 1. Title Extraction
        title_elem = card.find(["h2", "h3", "h4", "a"], class_=lambda c: c and any(k in str(c).lower() for k in ["title", "name", "heading"]))
        title = title_elem.get_text(strip=True) if title_elem else "RV Unit"

        # 2. Price Extraction
        price = None
        price_elem = card.find(class_=lambda c: c and any(k in str(c).lower() for k in ["sale-price", "our-price", "special-price", "price"]))
        if price_elem:
            price = clean_price(price_elem.get_text(strip=True))
        if not price:
            # Fallback regex search for currency formats
            matches = re.findall(r"\$\s?([0-9]{2,3},[0-9]{3})", card_text)
            if matches:
                price = clean_price(matches[0])

        if not price:
            continue

        # 3. Stock / VIN Extraction
        stock = "N/A"
        stock_match = re.search(r"(?:Stock|STK|VIN)\s*#?:?\s*([A-Za-z0-9\-]+)", card_text, re.IGNORECASE)
        if stock_match:
            stock = stock_match.group(1)

        # 4. Link
        link_elem = card.find("a", href=True)
        link = urljoin(target["url"], link_elem["href"]) if link_elem else target["url"]

        units.append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "dealer": target["dealer"],
            "target_model": target["model"],
            "listing_title": title,
            "price": price,
            "msrp": "",
            "stock": stock,
            "availability": "In Stock",
            "url": link,
            "source_url": target["url"]
        })

    return units

def scrape_with_playwright(browser, target):
    """Load target URL in headless browser, handle hydration delays, and extract DOM."""
    logging.info(f"Visiting {target['dealer']} -> {target['model']}...")
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800}
    )
    page = context.new_page()

    try:
        # Load page and wait for DOM network idle
        page.goto(target["url"], timeout=35000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)  # 3-second grace for hydration of React/Vue inventory cards

        # Gentle scroll down to trigger any lazy-loaded inventory grids
        page.evaluate("window.scrollBy(0, 700)")
        page.wait_for_timeout(1000)

        html_content = page.content()
        listings = parse_rendered_html(html_content, target)
        logging.info(f"Found {len(listings)} listings on {target['dealer']}")
        return listings

    except PlaywrightTimeoutError:
        logging.warning(f"Timeout on {target['url']}")
        return []
    except Exception as e:
        logging.error(f"Scrape error on {target['dealer']}: {e}")
        return []
    finally:
        context.close()

def log_to_csv(records):
    """Append records to persistent CSV log."""
    file_exists = os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > 0
    with open(LOG_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        for r in records:
            writer.writerow(r)
            
def send_discord_alert(unit, old_price=None):
    """Sends a rich embedded notification to a Discord channel."""
    if not DISCORD_WEBHOOK_URL:
        return

    # Determine status & embed color
    if old_price and unit["price"] < old_price:
        drop = old_price - unit["price"]
        title = f"PRICE DROP: {unit['target_model']}"
        desc = f"**${drop:,} Price Cut!**\nOld Price: ~~${old_price:,}~~\n**New Price: ${unit['price']:,}**"
        color = 5763719  # Bright Green
    else:
        title = f"New Listing: {unit['target_model']}"
        desc = f"**Price: ${unit['price']:,}**"
        color = 3447003  # Blue

    payload = {
        "username": "RV Tracker",
        "avatar_url": "https://i.imgur.com/4M34hi2.png",
        "embeds": [
            {
                "title": title,
                "url": unit.get("url", ""),
                "description": desc,
                "color": color,
                "fields": [
                    {"name": "Dealership", "value": unit["dealer"], "inline": True},
                    {"name": "Stock / VIN", "value": unit.get("stock", "N/A"), "inline": True},
                    {"name": "Listing Title", "value": unit.get("listing_title", "N/A"), "inline": False}
                ],
                "footer": {"text": "Regional Inventory Alert (NY/PA/OH)"}
            }
        ]
    }

    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        if resp.status_code not in (200, 204):
            print(f"Discord alert error: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"Failed to push to Discord: {e}")

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        all_found = []

        for target in TARGET_SEARCHES:
            listings = scrape_with_playwright(browser, target)
            for unit in listings:
                send_discord_alert(unit)
                all_found.append(unit)

        browser.close()

    if all_found:
        log_to_csv(all_found)
        logging.info(f"Successfully recorded {len(all_found)} units to {LOG_FILE}")
    else:
        logging.info("No units parsed in this run.")

if __name__ == "__main__":
    main()