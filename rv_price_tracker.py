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
import time
import logging
from datetime import datetime
import requests
from bs4 import BeautifulSoup

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

def parse_listing(card, dealer):
    """
    Generic card extractor for standard Dealer Spike / RV dealership CMS layouts.
    Adapts across Wilkins, Meyers, and Colton common HTML schemas.
    """
    title, price, vin_stock = "Unknown Unit", None, "N/A"
    
    # 1. Extract Title
    title_elem = card.find(["h2", "h3", "h4", "a"], class_=lambda c: c and any(k in str(c).lower() for k in ["title", "unit-title", "name"]))
    if title_elem:
        title = title_elem.get_text(strip=True)
        
    # 2. Extract Price (Sale price / Our price preferred over MSRP)
    price_elem = card.find(class_=lambda c: c and any(k in str(c).lower() for k in ["sale-price", "our-price", "unit-price", "special-price", "price"]))
    if price_elem:
        price = clean_price(price_elem.get_text(strip=True))
        
    # 3. Extract Stock or VIN
    stock_elem = card.find(class_=lambda c: c and any(k in str(c).lower() for k in ["stock", "vin"]))
    if stock_elem:
        vin_stock = stock_elem.get_text(strip=True)
        
    return {
        "title": title,
        "price": price,
        "stock": vin_stock,
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

def check_dealership(target):
    """Fetch search page and parse unit cards."""
    logging.info(f"Checking {target['dealer']} for {target['model']}...")
    try:
        resp = requests.get(target["url"], headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logging.warning(f"Failed to fetch {target['url']} (Status: {resp.status_code})")
            return []
            
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Most dealer CMSs wrap units in listing cards, but the class names vary.
        cards = find_listing_cards(soup)
        
        results = []
        for card in cards:
            data = parse_listing(card, target["dealer"])
            if data["price"]:
                results.append({
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "dealer": target["dealer"],
                    "target_model": target["model"],
                    "listing_title": data["title"],
                    "price": data["price"],
                    "stock": data["stock"],
                    "url": target["url"]
                })
        return results
    except Exception as e:
        logging.error(f"Error scraping {target['dealer']}: {e}")
        return []

def log_to_csv(records):
    """Append new listings to persistent CSV file."""
    file_exists = os.path.isfile(LOG_FILE)
    fieldnames = ["timestamp", "dealer", "target_model", "listing_title", "price", "stock", "url"]
    
    with open(LOG_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for r in records:
            writer.writerow(r)
    logging.info(f"Appended {len(records)} records to {LOG_FILE}")

def main():
    for target in TARGET_SEARCHES:
        units = check_dealership(target)
        for unit in units:
            log_to_csv([unit])
        time.sleep(2)  # Polite crawling delay between requests

    logging.info("Tracking run complete.")

if __name__ == "__main__":
    main()
