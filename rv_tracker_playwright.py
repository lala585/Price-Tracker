"""
RV Price & Inventory Tracker (Playwright + Resilient DOM Diagnostics)
======================================================================
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
     # --- Forest River r-pod RP-190 ---
    {
        "dealer": "Wilkins RV",
        "model": "Forest River r-pod RP-190",
        "url": "https://www.wilkinsrv.com/rv-search?s=true&manufacturer=forest+river+rv&brand=r+pod&keyword=RP-190"
    },
    {
        "dealer": "Colton RV",
        "model": "Forest River r-pod RP-190",
        "url": "https://www.coltonrv.com/product/travel-trailer?s=true&manufacturer=forest+river+rv&brand=r+pod&keyword=RP-190"
    },
    {
        "dealer": "Seven Os RV",
        "model": "Forest River r-pod RP-190",
        "url": "https://www.sevenos.com/rv-search?s=true&brand=r+pod&keyword=RP-190"
    },
    {
        "dealer": "Meyer's RV",
        "model": "Forest River r-pod RP-190",
        "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&types=29&brand=r+pod&zip=14202&distance=200&lots=1109%2C1113%2C1724%2C1114%2C1116%2C1117%2C1390%2C1118"
    },
    # --- Forest River Flagstaff E-Pro E19FBS ---
    {
        "dealer": "Wilkins RV",
        "model": "Forest River Flagstaff E-Pro E19FBS",
        "url": "https://www.wilkinsrv.com/rv-search?s=true&manufacturer=forest+river+rv&brand=flagstaff+e+pro&keyword=E19FBS"
    },
    {
        "dealer": "Colton RV",
        "model": "Forest River Flagstaff E-Pro E19FBS",
        "url": "https://www.coltonrv.com/product/travel-trailer?s=true&manufacturer=forest+river+rv&brand=flagstaff+e+pro&keyword=E19FBS"
    },
    {
        "dealer": "Seven Os RV",
        "model": "Forest River Flagstaff E-Pro E19FBS",
        "url": "https://www.sevenos.com/rv-search?s=true&brand=flagstaff+e+pro&keyword=E19FBS"
    },
    {
        "dealer": "Meyer's RV",
        "model": "Forest River Flagstaff E-Pro E19FBS",
        "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&types=29&brand=flagstaff+e+pro&zip=14202&distance=200&lots=1109%2C1113%2C1724%2C1114%2C1116%2C1117%2C1390%2C1118"
    },

    # --- Forest River r-pod RP-198 ---
    {
        "dealer": "Wilkins RV",
        "model": "Forest River r-pod RP-198",
        "url": "https://www.wilkinsrv.com/rv-search?s=true&manufacturer=forest+river+rv&brand=r+pod&keyword=RP-198"
    },
    {
        "dealer": "Colton RV",
        "model": "Forest River r-pod RP-198",
        "url": "https://www.coltonrv.com/product/travel-trailer?s=true&manufacturer=forest+river+rv&brand=r+pod&keyword=RP-198"
    },
    {
        "dealer": "Seven Os RV",
        "model": "Forest River r-pod RP-198",
        "url": "https://www.sevenos.com/rv-search?s=true&brand=r+pod&keyword=RP-198"
    },
    {
        "dealer": "Meyer's RV",
        "model": "Forest River r-pod RP-198",
        "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&types=29&brand=r+pod&zip=14202&distance=200&lots=1109%2C1113%2C1724%2C1114%2C1116%2C1117%2C1390%2C1118"
    },
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
        time.sleep(0.5)  # Avoid hitting Discord rate limits
        if resp.status_code not in (200, 204):
            logging.warning(f"Discord webhook error {resp.status_code}: {resp.text}")
    except Exception as e:
        logging.error(f"Discord ping failed: {e}")


def parse_rendered_html(html, target):
    """Extract listing information with broad Dealer Spike, NetSource, and DOM structure support."""
    soup = BeautifulSoup(html, "html.parser")
    units = []

    # Broad structural selectors matching Dealer Spike (v7/v8), NetSource, RVT, and WordPress layouts
    card_selectors = [
        "[data-unit-id]",
        "li.unit",
        "li[class*='unit']",
        "div[class*='unit-']",
        "div[class*='-unit']",
        "div[class*='v7list-item']",
        "div[class*='vehicle-']",
        "div[class*='inventory-item']",
        "li[class*='inventory-item']",
        "div[class*='unit-tile']",
        "div[class*='search-result']",
        "div[class*='listing']",
        "article"
    ]

    cards = []
    seen = set()
    for selector in card_selectors:
        for el in soup.select(selector):
            if id(el) not in seen and not any(id(p) in seen for p in el.parents):
                seen.add(id(el))
                cards.append(el)

    # Content-Driven Fallback: Find elements containing prices if class selectors failed
    if not cards:
        price_patterns = re.compile(r"\$\s?[0-9]{2,3},[0-9]{3}")
        for elem in soup.find_all(["div", "li", "article"]):
            if elem.name in ["div", "li", "article"] and price_patterns.search(elem.get_text()):
                text_len = len(elem.get_text())
                if 80 < text_len < 3000:
                    if id(elem) not in seen and not any(id(p) in seen for p in elem.parents):
                        seen.add(id(elem))
                        cards.append(elem)

    logging.info(f"[{target['dealer']}] Found {len(cards)} candidate containers.")

    if not cards:
        no_results_text = soup.find(string=re.compile(r"no (results|units|vehicles|inventory) found", re.I))
        if no_results_text:
            logging.info(f"[{target['dealer']}] Confirmed empty search result: '{no_results_text.strip()}'")
        else:
            logging.warning(f"[{target['dealer']}] 0 containers found. Inspect the saved debug HTML snapshot.")
        return []

    dollar_count = 0
    price_parsed_count = 0

    for idx, card in enumerate(cards):
        card_text = card.get_text(" ", strip=True)
        if "$" not in card_text:
            continue
        dollar_count += 1

        # 1. Price Extraction
        price = None
        price_elem = card.find(class_=lambda c: c and any(k in str(c).lower() for k in ["sale-price", "our-price", "special-price", "unit-price", "price"]))
        if price_elem:
            price = clean_price(price_elem.get_text(strip=True))
        if not price:
            matches = re.findall(r"\$\s?([0-9]{2,3},[0-9]{3})", card_text)
            if matches:
                valid_prices = [clean_price(m) for m in matches if clean_price(m) and clean_price(m) > 5000]
                if valid_prices:
                    price = min(valid_prices)

        if not price:
            continue

        price_parsed_count += 1

        # 2. Title Extraction
        title_elem = card.find(["h2", "h3", "h4", "a"], class_=lambda c: c and any(k in str(c).lower() for k in ["title", "name", "heading"]))
        title = title_elem.get_text(strip=True) if title_elem else ""
        if not title:
            m = re.search(r"(202[0-9]\s+[A-Za-z0-9\s\-]+)", card_text)
            title = m.group(1).strip() if m else target["model"]

        # 3. Stock / VIN Extraction
        stock = "N/A"
        stock_match = re.search(r"(?:Stock|STK|VIN|Unit\s*#?)\s*#?:?\s*([A-Za-z0-9\-]+)", card_text, re.IGNORECASE)
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

    logging.info(f"[{target['dealer']}] Parsed {len(units)} units from {len(cards)} containers.")
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
        logging.info(f"[{target['dealer']}] Page loaded. HTTP Status: {http_status} | Final URL: {page.url}")

        # Dismiss location/cookie overlays if present
        for btn_text in ["Accept", "Close", "Agree", "Continue"]:
            try:
                btn = page.locator(f"button:has-text('{btn_text}')").first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    logging.info(f"[{target['dealer']}] Dismissed modal button: '{btn_text}'")
            except Exception:
                pass

        try:
            page.wait_for_selector("[class*='price'], [class*='unit'], [class*='vehicle'], [data-unit-id]", timeout=6000)
            logging.info(f"[{target['dealer']}] Selector wait resolved (found listing/price container).")
        except Exception:
            logging.warning(f"[{target['dealer']}] Timed out waiting 6s for price/unit selectors. Page title: '{page.title()}'")

        page.evaluate("window.scrollBy(0, 700)")
        page.wait_for_timeout(1000)

        html_content = page.content()
        listings = parse_rendered_html(html_content, target)

        if not listings:
            safe_dealer = re.sub(r"\W+", "_", target["dealer"].lower())
            safe_model = re.sub(r"\W+", "_", target["model"].lower())
            debug_filename = f"debug_{safe_dealer}_{safe_model}.html"
            with open(debug_filename, "w", encoding="utf-8") as f:
                f.write(html_content)
            logging.info(f"[{target['dealer']}] Saved DOM snapshot to {debug_filename} (HTML size: {len(html_content)} bytes)")
        else:
            logging.info(f"[{target['dealer']}] Successfully recorded {len(listings)} listings.")

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
        logging.info(f"Successfully recorded {len(all_found)} total units to {LOG_FILE}")
    else:
        logging.info("No units parsed across any dealer search in this run.")


if __name__ == "__main__":
    main()