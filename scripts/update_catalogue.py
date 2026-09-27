#!/usr/bin/env python3
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "catalogue.json"
BASE_URL = "https://www.singersl.com"
USER_AGENT = (
    "Mozilla/5.0 (compatible; SingerCustomerRecommendationBot/2.0; "
    "+https://github.com/) "
)
LISTING_URLS = [
    f"{BASE_URL}/products?listview=true&order_by=nf&page={page}"
    for page in range(1, 41)
]
PRICE_RE = re.compile(r"(?:Rs\.?|LKR)\s*([0-9][0-9,]*)", re.I)
PCT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*Off", re.I)
OFFER_RE = re.compile(
    r"(?:Today's Sale\s*-\s*)?(?:Offer\s*-\s*|Special Discount\s*-\s*|Offers?:\s*)([^\n]+)",
    re.I,
)
BRANDS = [
    "Singer", "Samsung", "Sony", "Whirlpool", "Beko", "Sisil",
    "ASUS", "LG", "Philips", "Panasonic", "Dell", "HP", "Acer",
    "Apple", "Oppo", "Vivo", "Redmi", "Realme", "Haier", "TCL"
]


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get(url: str, timeout: int = 25):
    return requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=timeout,
    )


def infer_category(text: str) -> str:
    s = (text or "").lower()
    rules = [
        ("Television", r"\btv\b|television|led tv|smart tv|google tv"),
        ("Refrigerator", r"refrigerator|fridge|freezer"),
        ("Washing Machine", r"washing|washer|wash machine"),
        ("Air Conditioner", r"air conditioner|\ba/c\b|btu"),
        ("Rice Cooker", r"rice cooker"),
        ("Kitchen Appliance", r"blender|grinder|mixer|kettle|oven|microwave|hood|toaster|air fryer|induction|cooker"),
        ("Audio", r"speaker|soundbar|home theatre|theatre bar|audio|sound system"),
        ("Mobile", r"mobile|smartphone|iphone|galaxy|oppo|vivo|redmi|realme"),
        ("Computer", r"laptop|notebook|computer|desktop|printer|tablet|ipad|asus|acer|dell|\bhp\b"),
        ("Furniture", r"sofa|table|chair|bed|mattress|wardrobe|cupboard|dressing|cabinet|furniture"),
        ("Water Pump", r"water pump|pump"),
        ("Sewing Machine", r"sewing machine|sewing"),
        ("Personal Care", r"trimmer|shaver|hair dryer|hair care|fitness|massager"),
        ("Fan", r"\bfan\b|ceiling fan|pedestal fan|stand fan"),
        ("Iron", r"\biron\b|steam iron"),
    ]
    for category, pattern in rules:
        if re.search(pattern, s, re.I):
            return category
    return "Other"


def numbers_from_value(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"[0-9][0-9,]*(?:\.[0-9]+)?", value.replace(" ", ""))
        if m:
            return float(m.group(0).replace(",", ""))
    return None


def parse_jsonld(soup: BeautifulSoup) -> list:
    found = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        found.append(data)
    return found


def iter_json_objects(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from iter_json_objects(x)
    elif isinstance(obj, dict):
        yield obj
        for value in obj.values():
            if isinstance(value, (dict, list)):
                yield from iter_json_objects(value)


def extract_product_from_page(url: str, html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    lines = [clean_spaces(x) for x in soup.get_text("\n", strip=True).splitlines() if clean_spaces(x)]
    page_text = "\n".join(lines)

    product_obj = None
    for data in parse_jsonld(soup):
        for obj in iter_json_objects(data):
            typ = obj.get("@type")
            types = typ if isinstance(typ, list) else [typ]
            if any(str(t).lower() == "product" for t in types if t):
                product_obj = obj
                break
        if product_obj:
            break

    title = clean_spaces(product_obj.get("name", "")) if product_obj else ""
    if not title:
        h1 = soup.find("h1")
        title = clean_spaces(h1.get_text(" ", strip=True)) if h1 else ""
    if not title:
        title = clean_spaces(soup.title.get_text(" ", strip=True)) if soup.title else ""

    sku = ""
    if product_obj:
        sku = clean_spaces(
            str(product_obj.get("sku") or product_obj.get("mpn") or product_obj.get("productID") or "")
        )

    offers = product_obj.get("offers") if product_obj else None
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        offers = {}

    price = numbers_from_value(offers.get("price"))
    if price is None:
        price_tag = soup.find("meta", attrs={"property": "product:price:amount"}) or soup.find("meta", attrs={"name": "product:price:amount"})
        if price_tag:
            price = numbers_from_value(price_tag.get("content"))

    mrp = None
    high = numbers_from_value(offers.get("highPrice"))
    if high is not None and price is not None and high > price:
        mrp = high

    discount = ""
    pct = PCT_RE.search(page_text)
    if pct:
        number = pct.group(1).rstrip("0").rstrip(".")
        discount = f"{number}% OFF"

    offers_found = []
    for match in OFFER_RE.finditer(page_text):
        offer = clean_spaces(match.group(1))
        if offer and offer not in offers_found:
            offers_found.append(offer)

    availability = str(offers.get("availability") or "").lower()
    in_stock = None
    if "instock" in availability or "limitedavailability" in availability:
        in_stock = True
    elif "outofstock" in availability or "soldout" in availability:
        in_stock = False
    elif re.search(r"\bout of stock\b|\bsold out\b", page_text, re.I):
        in_stock = False

    if not sku:
        slug = urlparse(url).path.rstrip("/").split("/")[-1]
        sku = re.sub(r"[^A-Za-z0-9]+", "-", slug).strip("-").upper()[:80]

    if not title or price is None:
        return None

    brand = ""
    for b in BRANDS:
        if re.search(rf"\b{re.escape(b)}\b", title, re.I):
            brand = b
            break

    return {
        "category": infer_category(title),
        "product": title,
        "sku": sku,
        "price": int(round(price)),
        "url": url,
        "mrp": int(round(mrp)) if mrp is not None else None,
        "discount": discount,
        "offerNote": " | ".join(offers_found[:3]),
        "brand": brand,
        "inStock": in_stock,
        "live": True,
        "sourceStatus": "Live Singer product page",
        "liveCheckedAt": now_iso(),
    }


def discover_product_urls() -> set[str]:
    urls: set[str] = set()

    def fetch_listing(url: str):
        try:
            response = get(url, timeout=20)
            response.raise_for_status()
            return response.text
        except Exception:
            return ""

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_listing, url) for url in LISTING_URLS]
        for future in as_completed(futures):
            html = future.result()
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href = anchor.get("href", "").strip()
                absolute = urljoin(BASE_URL, href)
                path = urlparse(absolute).path.lower()
                if path.startswith("/product/"):
                    urls.add(absolute.split("#", 1)[0])
    return urls


def seed_existing_urls(payload: dict) -> set[str]:
    urls = set()
    for item in payload.get("products", []):
        url = str(item.get("url", "")).strip()
        if "/product/" in url.lower():
            urls.add(url.split("#", 1)[0])
    return urls


def parse_urls(urls: set[str], fallback_by_url: dict[str, dict]) -> list[dict]:
    def fetch_one(url: str):
        try:
            response = get(url, timeout=25)
            response.raise_for_status()
            return url, response.text
        except Exception:
            return url, ""

    results = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(fetch_one, url) for url in sorted(urls)]
        for future in as_completed(futures):
            url, html = future.result()
            if not html:
                continue
            parsed = extract_product_from_page(url, html)
            if parsed:
                results.append(parsed)
            elif url in fallback_by_url:
                fallback = dict(fallback_by_url[url])
                fallback["live"] = False
                fallback["sourceStatus"] = "Fallback retained"
                fallback["liveError"] = "Product page could not be parsed"
                results.append(fallback)
    return results


def fallback_products(payload: dict) -> list[dict]:
    out = []
    for item in payload.get("products", []):
        if not item.get("product"):
            continue
        row = dict(item)
        row.setdefault("live", False)
        row.setdefault("sourceStatus", "Fallback retained")
        out.append(row)
    return out


def main() -> None:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    existing = fallback_products(payload)
    fallback_by_url = {
        str(x.get("url", "")).strip(): x for x in existing if str(x.get("url", "")).strip()
    }

    discovered_urls = discover_product_urls()
    urls = discovered_urls | seed_existing_urls(payload)
    parsed = parse_urls(urls, fallback_by_url)

    merged: dict[str, dict] = {}
    for item in existing + parsed:
        sku = clean_spaces(str(item.get("sku", ""))).upper()
        url = clean_spaces(str(item.get("url", "")))
        key = sku or url
        if not key:
            continue

        current = merged.get(key)
        # Prefer a successful live page over fallback data.
        if current is None or (item.get("live") and not current.get("live")):
            merged[key] = item
        elif item.get("live") and current.get("live"):
            merged[key] = item

    products = list(merged.values())

    # Stable output for clean diffs and a predictable app order.
    products.sort(key=lambda x: (str(x.get("category", "Other")), str(x.get("product", ""))))

    live_count = sum(1 for x in products if x.get("live"))
    stock_known = sum(1 for x in products if x.get("live") and x.get("inStock") is not None)
    in_stock = sum(1 for x in products if x.get("live") and x.get("inStock") is True)

    checked_at = now_iso()
    out = {
        **payload,
        "schemaVersion": 2,
        "checkedAt": checked_at,
        "source": f"{BASE_URL}/products",
        "refreshMethod": "Dynamic Singer public catalogue discovery + exact product pages + structured ecommerce data",
        "refreshFrequency": "hourly",
        "products": products,
        "summary": {
            "total": len(products),
            "liveUpdated": live_count,
            "fallbackRetained": len([x for x in products if not x.get("live")]),
            "productUrlsDiscovered": len(discovered_urls),
            "stockStatusKnown": stock_known,
            "inStock": in_stock,
        },
        "notes": [
            "The updater discovers public Singer product pages from the catalogue listing and supplements them with previously saved product URLs.",
            "Current price, MRP, discount and visible offers are read from structured ecommerce data where available.",
            "Stock availability is collected for internal recommendation filtering only and is never included in customer-facing messages.",
            "If a specific product lookup fails, the previous saved product value is retained.",
            "The customer app processes uploaded transaction spreadsheets locally in the browser; the spreadsheet is not uploaded by the page.",
        ],
    }

    CATALOG_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        "Catalogue refresh complete: "
        f"{live_count}/{len(products)} live products; "
        f"{len(discovered_urls)} public product URLs discovered; "
        f"{in_stock} currently in stock where stock status was available."
    )


if __name__ == "__main__":
    main()
