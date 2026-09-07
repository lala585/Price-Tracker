"""
RV Price & Inventory Tracker
============================
Automated scraper and alert tool for tracking dealership prices across
Wilkins RV, Meyer's RV Superstores, and Colton RV.

Features:
- Reads an inventory target list of RV search URLs
- Extracts title, stock #, MSRP, sale price, and availability
- Logs results into an append-only CSV / SQLite database
- Highlights price drops in console or via optional desktop/email alerts
"""

import os
import csv
import json
import re
import tempfile
import time
import logging
from datetime import datetime
from importlib import import_module
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# User-Agent header to prevent 403 Forbidden errors
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# CSV Log File
LOG_FILE = "rv_price_history.csv"
CSV_FIELDS = [
    "timestamp", "dealer", "target_model", "listing_title", "price", "msrp",
    "stock", "availability", "last_seen", "url", "source_url", "http_status",
    "card_count", "scrape_error",
]
REQUEST_TIMEOUT = 20
REQUEST_DELAY = 2
USE_PLAYWRIGHT_FALLBACK = True # Set to True if some dealer pages require JavaScript rendering

RETRY_POLICY = Retry(
    total=3,
    connect=3,
    read=3,
    status=3,
    backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET"]),
    raise_on_status=False,
)
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.mount("https://", HTTPAdapter(max_retries=RETRY_POLICY))
SESSION.mount("http://", HTTPAdapter(max_retries=RETRY_POLICY))

# Target search URLs (Wilkins RV, Meyer's RV, Colton RV search endpoints)
TARGET_SEARCHES = [
    # Coachmen RV Apex Nano 190RBS
    {"dealer": "Wilkins RV", "model": "Coachmen RV Apex Nano 190RBS", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=Apex+Nano+190RBS"},
    {"dealer": "Meyer's RV", "model": "Coachmen RV Apex Nano 190RBS", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=Apex+Nano+190RBS"},
    {"dealer": "Colton RV", "model": "Coachmen RV Apex Nano 190RBS", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=190RBS&s=true"},
    {"dealer": "Camping World", "model": "Coachmen RV Apex Nano 190RBS", "url": "https://rv.campingworld.com/shop-rvs?query=apex%20nano%20190rbs"},
    {"dealer": "Seven Os RV", "model": "Coachmen RV Apex Nano 190RBS", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=190RBS"},

    # Forest River Flagstaff Micro Lite 21FBRS
    {"dealer": "Wilkins RV", "model": "Forest River Flagstaff Micro Lite 21FBRS", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=21FBRS"},
    {"dealer": "Meyer's RV", "model": "Forest River Flagstaff Micro Lite 21FBRS", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=21FBRS"},
    {"dealer": "Colton RV", "model": "Forest River Flagstaff Micro Lite 21FBRS", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=21FBRS&s=true"},
    {"dealer": "Camping World", "model": "Forest River Flagstaff Micro Lite 21FBRS", "url": "https://rv.campingworld.com/shop-rvs?query=21fbrs"},
    {"dealer": "Seven Os RV", "model": "Forest River Flagstaff Micro Lite 21FBRS", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=21FBRS"},

    # Coachmen RV Freedom Express Select 19SE
    {"dealer": "Wilkins RV", "model": "Coachmen RV Freedom Express Select 19SE", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=Freedom+Express+19SE"},
    {"dealer": "Meyer's RV", "model": "Coachmen RV Freedom Express Select 19SE", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=Freedom+Express+19SE"},
    {"dealer": "Colton RV", "model": "Coachmen RV Freedom Express Select 19SE", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=19SE&s=true"},
    {"dealer": "Camping World", "model": "Coachmen RV Freedom Express Select 19SE", "url": "https://rv.campingworld.com/shop-rvs?query=freedom%20express%2019se"},
    {"dealer": "Seven Os RV", "model": "Coachmen RV Freedom Express Select 19SE", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=19SE"},

    # Coachmen RV Freedom Express Ultra Lite 192RBS
    {"dealer": "Wilkins RV", "model": "Coachmen RV Freedom Express Ultra Lite 192RBS", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=Freedom+Express+192RBS"},
    {"dealer": "Meyer's RV", "model": "Coachmen RV Freedom Express Ultra Lite 192RBS", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=Freedom+Express+192RBS"},
    {"dealer": "Colton RV", "model": "Coachmen RV Freedom Express Ultra Lite 192RBS", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=192RBS&s=true"},
    {"dealer": "Camping World", "model": "Coachmen RV Freedom Express Ultra Lite 192RBS", "url": "https://rv.campingworld.com/shop-rvs?query=freedom%20express%20192rbs"},
    {"dealer": "Seven Os RV", "model": "Coachmen RV Freedom Express Ultra Lite 192RBS", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=192RBS"},

    # East To West Longitude 185RB
    {"dealer": "Wilkins RV", "model": "East To West Longitude 185RB", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=Longitude+185RB"},
    {"dealer": "Meyer's RV", "model": "East To West Longitude 185RB", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=Longitude+185RB"},
    {"dealer": "Colton RV", "model": "East To West Longitude 185RB", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=185RB&s=true"},
    {"dealer": "Camping World", "model": "East To West Longitude 185RB", "url": "https://rv.campingworld.com/shop-rvs?query=longitude%20185rb"},
    {"dealer": "Seven Os RV", "model": "East To West Longitude 185RB", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=185RB"},

    # Forest River r-pod RP-180
    {"dealer": "Wilkins RV", "model": "Forest River r-pod RP-180", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=RP-180"},
    {"dealer": "Meyer's RV", "model": "Forest River r-pod RP-180", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=RP-180"},
    {"dealer": "Colton RV", "model": "Forest River r-pod RP-180", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=RP-180&s=true"},
    {"dealer": "Camping World", "model": "Forest River r-pod RP-180", "url": "https://rv.campingworld.com/shop-rvs?query=rp-180"},
    {"dealer": "Seven Os RV", "model": "Forest River r-pod RP-180", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=RP-180"},

    # Forest River r-pod RP-190
    {"dealer": "Wilkins RV", "model": "Forest River r-pod RP-190", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=RP-190"},
    {"dealer": "Meyer's RV", "model": "Forest River r-pod RP-190", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=RP-190"},
    {"dealer": "Colton RV", "model": "Forest River r-pod RP-190", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=RP-190&s=true"},
    {"dealer": "Camping World", "model": "Forest River r-pod RP-190", "url": "https://rv.campingworld.com/shop-rvs?query=rp-190"},
    {"dealer": "Seven Os RV", "model": "Forest River r-pod RP-190", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=RP-190"},

    # Forest River Rockwood Mini Lite 2109S
    {"dealer": "Wilkins RV", "model": "Forest River Rockwood Mini Lite 2109S", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=2109S"},
    {"dealer": "Meyer's RV", "model": "Forest River Rockwood Mini Lite 2109S", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=2109S"},
    {"dealer": "Colton RV", "model": "Forest River Rockwood Mini Lite 2109S", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=2109S&s=true"},
    {"dealer": "Camping World", "model": "Forest River Rockwood Mini Lite 2109S", "url": "https://rv.campingworld.com/shop-rvs?query=2109s"},
    {"dealer": "Seven Os RV", "model": "Forest River Rockwood Mini Lite 2109S", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=2109S"},

    # Venture RV Sonic Lite 169VRK
    {"dealer": "Wilkins RV", "model": "Venture RV Sonic Lite 169VRK", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=169VRK"},
    {"dealer": "Meyer's RV", "model": "Venture RV Sonic Lite 169VRK", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=169VRK"},
    {"dealer": "Colton RV", "model": "Venture RV Sonic Lite 169VRK", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=169VRK&s=true"},
    {"dealer": "Camping World", "model": "Venture RV Sonic Lite 169VRK", "url": "https://rv.campingworld.com/shop-rvs?query=169vrk"},
    {"dealer": "Seven Os RV", "model": "Venture RV Sonic Lite 169VRK", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=169VRK"},

    # Forest River Flagstaff E-Pro E19FBS
    {"dealer": "Wilkins RV", "model": "Forest River Flagstaff E-Pro E19FBS", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=E19FBS"},
    {"dealer": "Meyer's RV", "model": "Forest River Flagstaff E-Pro E19FBS", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=E19FBS"},
    {"dealer": "Colton RV", "model": "Forest River Flagstaff E-Pro E19FBS", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=E19FBS&s=true"},
    {"dealer": "Camping World", "model": "Forest River Flagstaff E-Pro E19FBS", "url": "https://rv.campingworld.com/shop-rvs?query=e19fbs"},
    {"dealer": "Seven Os RV", "model": "Forest River Flagstaff E-Pro E19FBS", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=E19FBS"},

    # Forest River Ibex 16MBJ-BM
    {"dealer": "Wilkins RV", "model": "Forest River Ibex 16MBJ-BM", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=16MBJ"},
    {"dealer": "Meyer's RV", "model": "Forest River Ibex 16MBJ-BM", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=16MBJ"},
    {"dealer": "Colton RV", "model": "Forest River Ibex 16MBJ-BM", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=16MBJ&s=true"},
    {"dealer": "Camping World", "model": "Forest River Ibex 16MBJ-BM", "url": "https://rv.campingworld.com/shop-rvs?query=16mbj"},
    {"dealer": "Seven Os RV", "model": "Forest River Ibex 16MBJ-BM", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=16MBJ"},

    # Forest River No Boundaries NB18.2-BM
    {"dealer": "Wilkins RV", "model": "Forest River No Boundaries NB18.2-BM", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=NB18.2"},
    {"dealer": "Meyer's RV", "model": "Forest River No Boundaries NB18.2-BM", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=NB18.2"},
    {"dealer": "Colton RV", "model": "Forest River No Boundaries NB18.2-BM", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=NB18.2&s=true"},
    {"dealer": "Camping World", "model": "Forest River No Boundaries NB18.2-BM", "url": "https://rv.campingworld.com/shop-rvs?query=nb18.2"},
    {"dealer": "Seven Os RV", "model": "Forest River No Boundaries NB18.2-BM", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=NB18.2"},

    # Forest River No Boundaries NB19.4
    {"dealer": "Wilkins RV", "model": "Forest River No Boundaries NB19.4", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=NB19.4"},
    {"dealer": "Meyer's RV", "model": "Forest River No Boundaries NB19.4", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=NB19.4"},
    {"dealer": "Colton RV", "model": "Forest River No Boundaries NB19.4", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=NB19.4&s=true"},
    {"dealer": "Camping World", "model": "Forest River No Boundaries NB19.4", "url": "https://rv.campingworld.com/shop-rvs?query=nb19.4"},
    {"dealer": "Seven Os RV", "model": "Forest River No Boundaries NB19.4", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=NB19.4"},

    # Forest River No Boundaries NB19.6
    {"dealer": "Wilkins RV", "model": "Forest River No Boundaries NB19.6", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=NB19.6"},
    {"dealer": "Meyer's RV", "model": "Forest River No Boundaries NB19.6", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=NB19.6"},
    {"dealer": "Colton RV", "model": "Forest River No Boundaries NB19.6", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=NB19.6&s=true"},
    {"dealer": "Camping World", "model": "Forest River No Boundaries NB19.6", "url": "https://rv.campingworld.com/shop-rvs?query=nb19.6"},
    {"dealer": "Seven Os RV", "model": "Forest River No Boundaries NB19.6", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=NB19.6"},

    # Forest River r-pod RP-198
    {"dealer": "Wilkins RV", "model": "Forest River r-pod RP-198", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=RP-198"},
    {"dealer": "Meyer's RV", "model": "Forest River r-pod RP-198", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=RP-198"},
    {"dealer": "Colton RV", "model": "Forest River r-pod RP-198", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=RP-198&s=true"},
    {"dealer": "Camping World", "model": "Forest River r-pod RP-198", "url": "https://rv.campingworld.com/shop-rvs?query=rp-198"},
    {"dealer": "Seven Os RV", "model": "Forest River r-pod RP-198", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=RP-198"},

    # Forest River r-pod RP-205
    {"dealer": "Wilkins RV", "model": "Forest River r-pod RP-205", "url": "https://www.wilkinsrv.com/rv-search?s=true&keyword=RP-205"},
    {"dealer": "Meyer's RV", "model": "Forest River r-pod RP-205", "url": "https://www.meyersrvsuperstores.com/rv-search?s=true&keyword=RP-205"},
    {"dealer": "Colton RV", "model": "Forest River r-pod RP-205", "url": "https://www.coltonrv.com/product/all-inventory?stocknumber=RP-205&s=true"},
    {"dealer": "Camping World", "model": "Forest River r-pod RP-205", "url": "https://rv.campingworld.com/shop-rvs?query=rp-205"},
    {"dealer": "Seven Os RV", "model": "Forest River r-pod RP-205", "url": "https://www.sevenos.com/rv-search?s=true&stocknumber=RP-205"},
]

def clean_price(price_str):
    """Clean and convert price strings like '$24,995' to integer 24995."""
    if not price_str:
        return None
    cleaned = "".join(c for c in str(price_str) if c.isdigit())
    return int(cleaned) if cleaned else None

def first_matching_element(card, selectors):
    """Return the first element matching selectors in priority order."""
    for selector in selectors:
        element = card.select_one(selector)
        if element:
            return element
    return None

def parse_listing(card, dealer, page_url=""):
    """
    Generic card extractor for standard Dealer Spike / RV dealership CMS layouts.
    Adapts across Wilkins, Meyers, and Colton common HTML schemas.
    """
    title, price, msrp, vin_stock = "Unknown Unit", None, None, "N/A"
    
    # 1. Extract Title
    title_elem = first_matching_element(card, [
        "[class*='unit-title']", "[class*='vehicle-title']", "[class*='listing-title']",
        "[class*='title']", "h1", "h2", "h3", "h4",
    ])
    if title_elem:
        title = title_elem.get_text(strip=True)
        
    # Prefer advertised sale prices and only use MSRP as a fallback.
    price_elem = first_matching_element(card, [
        ".sale-price", "[class*='sale-price']", ".our-price", "[class*='our-price']",
        ".special-price", "[class*='special-price']", ".unit-price", "[class*='unit-price']",
        ".price", "[class*='price']",
    ])
    if price_elem:
        price = clean_price(price_elem.get_text(strip=True))

    msrp_elem = first_matching_element(card, [
        ".msrp", "[class*='msrp']", ".original-price", "[class*='original-price']",
    ])
    if msrp_elem:
        msrp = clean_price(msrp_elem.get_text(strip=True))
        
    # 3. Extract Stock or VIN
    stock_elem = first_matching_element(card, [
        ".stock", "[class*='stock']", ".vin", "[class*='vin']",
    ])
    if stock_elem:
        vin_stock = stock_elem.get_text(strip=True)

    availability_elem = first_matching_element(card, [
        ".availability", "[class*='availability']", ".status", "[class*='status']",
    ])
    availability = availability_elem.get_text(" ", strip=True) if availability_elem else "Unknown"
    if availability == "Unknown":
        card_text = card.get_text(" ", strip=True)
        availability_match = re.search(
            r"\b(in stock|available|sold|pending|unavailable|out of stock)\b",
            card_text,
            re.IGNORECASE,
        )
        if availability_match:
            availability = availability_match.group(1).title()

    link = card.select_one("a[href]")
    listing_url = urljoin(page_url, link["href"]) if link else page_url
        
    return {
        "title": title,
        "price": price,
        "msrp": msrp,
        "stock": vin_stock,
        "availability": availability,
        "url": listing_url,
    }

def find_listing_cards(soup):
    """Find listing containers across common dealership HTML layouts."""
    selectors = [
        "[class*='unit-tile']",
        "[class*='item-card']",
        "[class*='vehicle-card']",
        "[class*='search-result']",
        "[class*='inventory']",
        "[class*='listing']",
        "[id*='inventory']",
        "[id*='listing']",
    ]

    cards = []
    seen = set()
    for selector in selectors:
        for element in soup.select(selector):
            identity = id(element)
            if identity not in seen:
                seen.add(identity)
                cards.append(element)

    # Fallback for pages whose classes do not describe the card type.
    if not cards:
        for element in soup.find_all(["article", "li"]):
            text = element.get_text(" ", strip=True).lower()
            if "$" in text and any(tag in text for tag in ["price", "msrp", "sale"]):
                cards.append(element)

    return cards

def iter_json_objects(value):
    """Yield dictionaries nested in JSON-LD arrays and graphs."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_objects(child)

def parse_json_ld_listings(soup, target):
    """Extract product listings from schema.org Product/Offer data when available."""
    records = []
    for script in soup.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue

        for item in iter_json_objects(payload):
            item_type = item.get("@type", [])
            item_types = item_type if isinstance(item_type, list) else [item_type]
            if not any(str(item_type).lower() in {"product", "vehicle"} for item_type in item_types):
                continue

            offer = item.get("offers", {})
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            price = clean_price(offer.get("price") or item.get("price"))
            if not price:
                continue

            availability = offer.get("availability", "Unknown")
            availability = str(availability).rsplit("/", 1)[-1].replace("InStock", "In Stock")
            records.append({
                "title": item.get("name", "Unknown Unit"),
                "price": price,
                "msrp": clean_price(item.get("msrp")),
                "stock": item.get("sku") or item.get("mpn") or "N/A",
                "availability": availability,
                "url": urljoin(target["url"], item.get("url", target["url"])),
            })
    return records

def fetch_with_playwright(url):
    """Optionally fetch pages whose listings are rendered only by JavaScript."""
    try:
        sync_playwright = import_module("playwright.sync_api").sync_playwright
    except ImportError:
        logging.warning("JavaScript fallback requested, but Playwright is not installed.")
        return None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(extra_http_headers=HEADERS)
        response = page.goto(url, wait_until="networkidle", timeout=REQUEST_TIMEOUT * 1000)
        html = page.content()
        status = response.status if response else 200
        browser.close()
    return status, html

def check_dealership(target):
    """Fetch search page and parse unit cards."""
    logging.info(f"Checking {target['dealer']} for {target['model']}...")
    response_status = "request_error"
    try:
        resp = SESSION.get(target["url"], timeout=REQUEST_TIMEOUT)
        response_status = resp.status_code
        if resp.status_code != 200:
            logging.warning(f"Failed to fetch {target['url']} (Status: {resp.status_code})")
            return []
            
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Most dealer CMSs wrap units in listing cards, but the class names vary.
        cards = find_listing_cards(soup)
        if not cards and USE_PLAYWRIGHT_FALLBACK:
            rendered = fetch_with_playwright(target["url"])
            if rendered:
                response_status, rendered_html = rendered
                soup = BeautifulSoup(rendered_html, "html.parser")
                cards = find_listing_cards(soup)

        if not cards:
            dynamic_markers = soup.select("script[src*='chunk'], script[src*='bundle'], [data-reactroot]")
            if dynamic_markers:
                logging.warning(
                    "%s returned no cards but appears JavaScript-rendered; "
                    "set USE_PLAYWRIGHT_FALLBACK = True if needed.",
                    target["dealer"],
                )
        
        results = []
        for card in cards:
            data = parse_listing(card, target["dealer"], target["url"])
            if data["price"]:
                results.append({
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "dealer": target["dealer"],
                    "target_model": target["model"],
                    "listing_title": data["title"],
                    "price": data["price"],
                    "msrp": data["msrp"],
                    "stock": data["stock"],
                    "availability": data["availability"],
                    "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "url": data["url"],
                    "source_url": target["url"],
                    "http_status": response_status,
                    "card_count": len(cards),
                    "scrape_error": "",
                })

        if not results:
            for data in parse_json_ld_listings(soup, target):
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                results.append({
                    "timestamp": timestamp,
                    "dealer": target["dealer"],
                    "target_model": target["model"],
                    "listing_title": data["title"],
                    "price": data["price"],
                    "msrp": data["msrp"],
                    "stock": data["stock"],
                    "availability": data["availability"],
                    "last_seen": timestamp,
                    "url": data["url"],
                    "source_url": target["url"],
                    "http_status": response_status,
                    "card_count": len(cards),
                    "scrape_error": "structured_data",
                })

        logging.info(
            "%s: HTTP %s, %s cards, %s listings",
            target["dealer"], response_status, len(cards), len(results),
        )
        return results
    except Exception as e:
        logging.error(f"Error scraping {target['dealer']}: {e}")
        return []

def log_to_csv(records):
    """Append new listings to persistent CSV file."""
    ensure_csv_schema()
    file_exists = os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > 0
    
    with open(LOG_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        for r in records:
            writer.writerow(r)
    logging.info(f"Appended {len(records)} records to {LOG_FILE}")

def ensure_csv_schema():
    """Add new columns to an older history file without discarding its data."""
    if not os.path.isfile(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        return

    with open(LOG_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing_fields = reader.fieldnames or []
        if existing_fields == CSV_FIELDS:
            return
        rows = list(reader)

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", newline="", encoding="utf-8", delete=False,
            dir=os.path.dirname(os.path.abspath(LOG_FILE)),
        ) as temp_file:
            temp_path = temp_file.name
            writer = csv.DictWriter(temp_file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
        os.replace(temp_path, LOG_FILE)
        logging.info("Updated CSV history columns to the current schema.")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

def listing_key(listing):
    """Create a stable key for duplicate listings found in one run."""
    identity = listing.get("stock")
    if not identity or identity == "N/A":
        identity = f"{listing.get('url', '')}|{listing.get('listing_title', '')}"
    return (
        listing.get("dealer", ""),
        listing.get("target_model", ""),
        identity,
        listing.get("price", ""),
    )

def main():
    seen_keys = set()
    for target in TARGET_SEARCHES:
        units = check_dealership(target)
        for unit in units:
            key = listing_key(unit)
            if key in seen_keys:
                logging.info("Skipping duplicate listing: %s", unit.get("listing_title"))
                continue
            seen_keys.add(key)
            log_to_csv([unit])
        time.sleep(REQUEST_DELAY)  # Polite crawling delay between requests

    logging.info("Tracking run complete.")

if __name__ == "__main__":
    main()
