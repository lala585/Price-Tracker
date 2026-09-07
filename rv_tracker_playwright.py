"""
RV Price & Inventory Tracker (Strict Model & Price Filter)
==========================================================
Automated scraper for dealership inventory across Wilkins RV,
Meyer's RV Superstores, Colton RV, and Seven O's RV.
"""

import os
import re
import csv
import json
import time
import logging
import requests
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

LOG_FILE = "rv_price_history.csv"
CSV_FIELDS = [
    "timestamp", "dealer", "target_model", "listing_title",
    "price", "msrp", "stock", "availability", "url", "source_url"
]

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

TARGET_SEARCHES = [
    # --- Coachmen RV Apex Nano 190RBS ---
    {
        "dealer": "Wilkins RV",
        "model": "Coachmen RV Apex Nano 190RBS",
        "model_key": "190RBS",
        "url": "https://www.wilkinsrv.com/rv-search?s=true&manufacturer=coachmen+rv&brand=apex+nano&keyword=190RBS"
    },
    {
        "dealer": "Colton RV",
        "model": "Coachmen RV Apex Nano 190RBS",
        "model_key": "190RBS",
        "url": "https://www.coltonrv.com/product/travel-trailer?s=true&manufacturer=coachmen+rv&brand=apex+nano&keyword=190RBS"
    },
    {
        "dealer": "Seven Os RV",
        "model": "Coachmen RV Apex Nano 190RBS",
        "model_key": "190RBS",
        "url": "https://www.sevenos.com/rv-search?s=true&brand=apex+nano&keyword=190RBS"
    },
    {
        "dealer": "Meyer's RV",
        "model": "Coachmen RV Apex Nano 190RBS",
        "model_key": "190RBS",
        "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&types=29&brand=apex+nano&keyword=190RBS&zip=14202&distance=200&lots=1109%2C1113%2C1724%2C1114%2C1116%2C1117%2C1390%2C1118"
    },

    # --- Forest River Flagstaff Micro Lite 21FBRS ---
    {
        "dealer": "Wilkins RV",
        "model": "Forest River Flagstaff Micro Lite 21FBRS",
        "model_key": "21FBRS",
        "url": "https://www.wilkinsrv.com/rv-search?s=true&manufacturer=forest+river+rv&brand=flagstaff+micro+lite&keyword=21FBRS"
    },
    {
        "dealer": "Colton RV",
        "model": "Forest River Flagstaff Micro Lite 21FBRS",
        "model_key": "21FBRS",
        "url": "https://www.coltonrv.com/product/travel-trailer?s=true&manufacturer=forest+river+rv&brand=flagstaff+micro+lite&keyword=21FBRS"
    },
    {
        "dealer": "Seven Os RV",
        "model": "Forest River Flagstaff Micro Lite 21FBRS",
        "model_key": "21FBRS",
        "url": "https://www.sevenos.com/rv-search?s=true&brand=flagstaff+micro+lite&keyword=21FBRS"
    },
    {
        "dealer": "Meyer's RV",
        "model": "Forest River Flagstaff Micro Lite 21FBRS",
        "model_key": "21FBRS",
        "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&types=29&brand=flagstaff+micro+lite&keyword=21FBRS&zip=14202&distance=200&lots=1109%2C1113%2C1724%2C1114%2C1116%2C1117%2C1390%2C1118"
    },
]


def extract_price(text):
    """Find isolated dollar prices in card text (e.g., '$17,595' -> 17595)."""
    if not text:
        return None
    # Match standard currency formats: $14,995 or $14995
    matches = re.findall(r"\$\s?([1-9][0-9]{1,2},[0-9]{3}|[1-9][0-9]{4,5})\b", text)
    valid_prices = []
    for m in matches:
        cleaned = int(m.replace(",", "").strip())
        if 8000 <= cleaned <= 180000:  # Sensible price boundary for lightweight travel trailers
            valid_prices.append(cleaned)
    return min(valid_prices) if valid_prices else None


def send_discord_alert(unit, old_price=None):
    """Optional webhook notification to Discord channel."""
    if not DISCORD_WEBHOOK_URL:
        return

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
            ],
            "footer": {"text": "Regional Inventory Tracker (NY/PA/OH)"}
        }]
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        time.sleep(0.5)
        if resp.status_code not in (200, 204):
            logging.warning(f"Discord webhook error {resp.status_code}: {resp.text}")
    except Exception as e:
        logging.error(f"Discord ping failed: {e}")


def parse_rendered_html(html, target):
    """Extract listing information and strictly verify the target floorplan matches."""
    soup = BeautifulSoup(html, "html.parser")
    units = []

    card_selectors = [
        "li.unit",
        "li[class*='unit-']",
        "div.v7list-item",
        "div[data-unit-id]",
        "div.inventory-item",
        "li.inventory-item",
        "div.unit-tile",
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

        # 1. Title Extraction
        title_elem = card.find(["h2", "h3", "h4", "a"], class_=lambda c: c and any(k in str(c).lower() for k in ["title", "name", "heading"]))
        raw_title = title_elem.get_text(strip=True) if title_elem else card_text[:120]
        # Clean title to first clean line
        title = raw_title.split("Stock")[0].split("VIN")[0].strip()

        # 2. Strict Model Filtering
        # Ensure the listing explicitly contains the target floorplan (e.g., '190', '19SE', '192RBS')
        combined_identity = f"{title} {card_text}".upper()
        clean_key = re.sub(r"[^A-Z0-9]", "", target_key)
        clean_identity = re.sub(r"[^A-Z0-9]", "", combined_identity)

        if clean_key and clean_key not in clean_identity:
            continue  # Discard unrelated models on the lot (e.g. RP-171 when tracking RP-190)

        # 3. Clean Price Extraction
        price = extract_price(card_text)
        if not price:
            continue

        # 4. Stock / VIN Extraction
        stock = "N/A"
        stock_match = re.search(r"(?:Stock|STK|VIN)\s*#?:?\s*([A-Za-z0-9\-]+)", card_text, re.IGNORECASE)
        if stock_match:
            stock = stock_match.group(1)

        # 5. Link
        link_elem = card.find("a", href=True)
        link = urljoin(target["url"], link_elem["href"]) if link_elem else target["url"]

        # De-duplicate entries already captured from inner elements
        if any(u["url"] == link and u["stock"] == stock for u in units):
            continue

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

    logging.info(f"[{target['dealer']}] Verified {len(units)} units matching floorplan '{target_key}'.")
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
        response = page.goto(target["url"], timeout=45000, wait_until="load")
        http_status = response.status if response else "No Response"

        # Dismiss location/cookie overlays if present
        for btn_text in ["Accept", "Close", "Agree", "Continue"]:
            try:
                btn = page.locator(f"button:has-text('{btn_text}')").first
                if btn.is_visible(timeout=1000):
                    btn.click()
            except Exception:
                pass

        try:
            page.wait_for_selector("[class*='price'], [class*='unit'], [data-unit-id]", timeout=6000)
        except Exception:
            pass

        page.evaluate("window.scrollBy(0, 700)")
        page.wait_for_timeout(1000)

        html_content = page.content()
        listings = parse_rendered_html(html_content, target)
        return listings

    except PlaywrightTimeoutError:
        logging.warning(f"Timeout navigating to {target['url']}")
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
        logging.info(f"Successfully recorded {len(all_found)} verified units to {LOG_FILE}")
    else:
        logging.info("No units parsed across any dealer search in this run.")


if __name__ == "__main__":
    main()