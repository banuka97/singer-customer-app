#!/usr/bin/env python3
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "catalogue.json"
BASE_URL = "https://www.singersl.com"

USER_AGENT = (
    "Mozilla/5.0 (compatible; SingerCustomerRecommendationBot/1.0; "
    "+https://github.com/) "
)

PRICE_RE = re.compile(r"(?:Rs\.?|LKR)\s*([0-9][0-9,]*)", re.I)
PCT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*Off", re.I)
OFFER_RE = re.compile(
    r"(?:Today's Sale\s*-\s*)?(?:Offer\s*-\s*|Special Discount\s*-\s*|Offers?:\s*)([^\n]+)",
    re.I,
)

# Known public Singer product URLs. These are used first so the updater does not
# depend entirely on search/listing page markup.
KNOWN_URLS = {
    "SLE32E6A": f"{BASE_URL}/product/singer-32-hd-tv-sle32e6a",
    "SLE40F600A": f"{BASE_URL}/product/singer-40-full-hd-tv-sle40f600a",
    "SLE43J890": f"{BASE_URL}/product/singer-43-full-hd-google-tv-sle43j890",
    "GEO-260NF-TGYF": f"{BASE_URL}/product/singer-geo-refrigerator-geo-260nf-tgyf-2-doors-no-frost-227l-yellow-floral",
    "GEO-200D-INV": f"{BASE_URL}/product/singer-geo-refrigerator-geo-200d-inv-2-doors-inverter-185l-silver",
    "SN-276INV": f"{BASE_URL}/product/singer-digital-inverter-refrigerator-sn-276inv-276l-silver",
    "SWM-SAR65": f"{BASE_URL}/product/singer-semi-automatic-washing-machine-swm-sar65-65kg",
    "SWM-FA80R": f"{BASE_URL}/product/singer-fully-automatic-washing-machine-top-loading-swm-fa80r-8kg",
    "SWM-FA70R": f"{BASE_URL}/product/singer-washing-machine-top-load-7kg",
    "SWM-FAR75GT": f"{BASE_URL}/product/singer-glass-top-fully-automatic-washing-machine-swm-far75gt-75kg",
    "SL-ELITE12": f"{BASE_URL}/product/sisil-air-conditioner-non-inverter-12000-btu-sl-elite12",
    "SL-ELITE18": f"{BASE_URL}/product/sisil-air-conditioner-non-inverter-18000-btu-sl-elite18",
    "WP-12KINV": f"{BASE_URL}/product/whirlpool-air-conditioner-inverter-12000-btu-wp-12kinv",
    "SMGAR50H19D1J": f"{BASE_URL}/product/samsung-air-conditioner-inverter-18000-btu-wi-fi-smgar50h19d1j",
    "SRC-1528HS": f"{BASE_URL}/product/singer-rice-cooker-28l",
    "SRC-2545HS": f"{BASE_URL}/product/singer-rice-cooker-45l",
    "B-CTB6250XH": f"{BASE_URL}/product/beko-telescopic-hood-60-cm-stainless-steel-b-ctb6250xh",
    "HT-B500": f"{BASE_URL}/product/sony-bravia-theatre-bar-5-250w-31ch-soundbar-with-powerful-wireless-subwoofer",
    "LF-ENZO-CT-WHT-WN-S": f"{BASE_URL}/product/enzo-center-table-white-and-walnut-lf-enzo-ct-wht-wn-s",
    "AS-14-I7-16-512-7658-SIL": f"{BASE_URL}/product/asus-vivobook-15-x1504vap-bq7658ws-14th-intel-core-7-150u-16gb-ddr5-ram-512gb-ssd-microsoft-office-home-cool-silver",
}

LISTING_URLS = [
    f"{BASE_URL}/products?listview=true&order_by=nf&page={page}"
    for page in range(1, 21)
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


def page_lines(html: str) -> tuple[BeautifulSoup, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    lines = []
    for raw in soup.get_text("\n", strip=True).splitlines():
        line = clean_spaces(raw)
        if line:
            lines.append(line)
    return soup, lines


def numbers_from_value(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"[0-9][0-9,]*(?:\.[0-9]+)?", value.replace(" ", ""))
        if m:
            return float(m.group(0).replace(",", ""))
    return None


def structured_product_data(soup: BeautifulSoup) -> tuple[float | None, float | None, str, str]:
    current = None
    mrp = None
    discount = ""
    offer_note = ""

    def inspect(obj):
        nonlocal current, mrp, discount, offer_note
        if isinstance(obj, list):
            for x in obj:
                inspect(x)
            return
        if not isinstance(obj, dict):
            return

        typ = obj.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        is_product = any(str(t).lower() == "product" for t in types if t)
        if is_product:
            offers = obj.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if isinstance(offers, dict):
                p = numbers_from_value(offers.get("price"))
                hp = numbers_from_value(offers.get("highPrice"))
                if p is not None:
                    current = p
                if hp is not None and hp > (current or 0):
                    mrp = hp

        if "price" in obj and current is None:
            p = numbers_from_value(obj.get("price"))
            if p is not None:
                current = p

        if "highPrice" in obj and mrp is None:
            hp = numbers_from_value(obj.get("highPrice"))
            if hp is not None and hp > (current or 0):
                mrp = hp

        text_blob = json.dumps(obj, ensure_ascii=False)
        pct = PCT_RE.search(text_blob)
        if pct and not discount:
            number = pct.group(1).rstrip("0").rstrip(".")
            discount = f"{number}% OFF"

    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text(strip=True)
        if not raw:
            continue
        try:
            inspect(json.loads(raw))
        except Exception:
            continue

    # Meta tags sometimes carry the exact ecommerce price.
    for key in ["product:price:amount", "og:price:amount", "price"]:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and current is None:
            p = numbers_from_value(tag.get("content"))
            if p is not None:
                current = p

    return current, mrp, discount, offer_note


def extract_values(text: str, result: dict, soup: BeautifulSoup | None = None) -> None:
    current = mrp = None
    discount = ""
    if soup is not None:
        current, mrp, discount, _ = structured_product_data(soup)

    if current is not None:
        result["price"] = int(round(current))
    else:
        # Narrow regex fallback: ignore delivery/shipping amounts by taking values
        # from the text section near the product title/code.
        prices = [
            int(m.group(1).replace(",", ""))
            for m in PRICE_RE.finditer(text)
            if int(m.group(1).replace(",", "")) > 1000
        ]
        if prices:
            result["price"] = prices[0]

    if mrp is not None:
        result["mrp"] = int(round(mrp))
    elif result.get("price"):
        candidates = [
            int(m.group(1).replace(",", ""))
            for m in PRICE_RE.finditer(text)
            if int(m.group(1).replace(",", "")) > int(result["price"])
        ]
        if candidates:
            result["mrp"] = min(candidates)

    if discount:
        result["discount"] = discount
    else:
        pct = PCT_RE.search(text)
        if pct:
            number = pct.group(1).rstrip("0").rstrip(".")
            result["discount"] = f"{number}% OFF"

    offers = []
    for match in OFFER_RE.finditer(text):
        offer = clean_spaces(match.group(1))
        if offer and offer not in offers:
            offers.append(offer)
    if offers:
        result["offerNote"] = " | ".join(offers[:3])


def discover_product_urls(session: requests.Session, wanted_skus: set[str]) -> dict[str, str]:
    found: dict[str, str] = {
        sku: url for sku, url in KNOWN_URLS.items() if sku in wanted_skus
    }

    def fetch_listing(url: str):
        try:
            resp = session.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
                timeout=20,
            )
            resp.raise_for_status()
            return resp.text
        except Exception:
            return ""

    missing = wanted_skus - set(found)
    if not missing:
        return found

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_listing, url) for url in LISTING_URLS]
        for future in as_completed(futures):
            html = future.result()
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            anchors = soup.find_all("a", href=True)

            for sku in list(missing):
                low = sku.lower()
                for anchor in anchors:
                    href = anchor.get("href", "")
                    label = clean_spaces(anchor.get_text(" ", strip=True))
                    if low in href.lower() or low in label.lower():
                        if "/product/" in href.lower():
                            found[sku] = urljoin(BASE_URL, href)
                            missing.discard(sku)
                            break

            if not missing:
                break

    return found


def parse_product_page(sku: str, url: str, fallback: dict) -> dict:
    result = dict(fallback)
    result["url"] = url
    result["live"] = False
    result["sourceStatus"] = "Fallback retained"

    try:
        resp = get(url, timeout=25)
        resp.raise_for_status()
        soup, lines = page_lines(resp.text)
        page_text = "\n".join(lines)

        if sku.lower() not in page_text.lower():
            raise ValueError("SKU not found on Singer product page")

        # Product pages may show multiple products/variants in text. Restrict
        # fallback price extraction to a window near the product code.
        idx = page_text.lower().find(sku.lower())
        block = page_text[max(0, idx - 800): idx + 5000] if idx >= 0 else page_text[:5000]
        extract_values(block, result, soup)

        for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5"]):
            txt = clean_spaces(heading.get_text(" ", strip=True))
            if sku.lower() in txt.lower() and len(txt) < 260:
                result["product"] = re.sub(
                    rf"\s*[-–—]\s*{re.escape(sku)}\b", "", txt, flags=re.I
                ).strip()
                break

        result["live"] = True
        result["sourceStatus"] = "Live Singer product page"
        result["liveCheckedAt"] = now_iso()
        result.pop("liveError", None)
        return result

    except Exception as exc:
        result["liveError"] = str(exc)
        return result


def main() -> None:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    products = payload.get("products", [])

    wanted = {
        str(item.get("sku", "")).strip()
        for item in products
        if str(item.get("sku", "")).strip()
    }

    # The old placeholder SKU was replaced with a real current Singer-listed laptop.
    wanted.discard("CATALOGUE")

    session = requests.Session()
    discovered = discover_product_urls(session, wanted)

    updated_by_sku: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = []
        for item in products:
            sku = str(item.get("sku", "")).strip()
            if sku.upper() == "CATALOGUE":
                continue
            url = discovered.get(sku)
            if url:
                futures.append(pool.submit(parse_product_page, sku, url, item))
            else:
                fallback = dict(item)
                fallback["live"] = False
                fallback["sourceStatus"] = "Fallback retained"
                fallback["liveError"] = "Product URL not discovered in Singer catalogue"
                updated_by_sku[sku] = fallback

        for future in as_completed(futures):
            result = future.result()
            updated_by_sku[str(result.get("sku", "")).strip()] = result

    updated = []
    for item in products:
        sku = str(item.get("sku", "")).strip()
        if sku.upper() == "CATALOGUE":
            continue
        updated.append(updated_by_sku.get(sku, item))

    live_count = sum(1 for x in updated if x.get("live"))
    checked_at = now_iso()

    out = {
        **payload,
        "schemaVersion": 1,
        "checkedAt": checked_at,
        "source": f"{BASE_URL}/products",
        "refreshMethod": "GitHub Actions catalogue crawl + exact product pages + structured ecommerce data",
        "refreshFrequency": "hourly",
        "products": updated,
        "summary": {
            "total": len(updated),
            "liveUpdated": live_count,
            "fallbackRetained": len(updated) - live_count,
            "productUrlsDiscovered": len(discovered),
        },
        "notes": [
            "The updater prefers known public Singer product URLs and supplements them by crawling catalogue pages.",
            "Current price/MRP are read from structured ecommerce data where available, avoiding delivery-fee amounts.",
            "Discounts and visible offers are also extracted from the product page.",
            "If an exact lookup fails, the previous saved value is retained.",
            "The obsolete CATALOGUE placeholder was replaced with a real Singer-listed laptop SKU.",
            "Stock is not used as a hard recommendation filter in the app.",
        ],
    }

    CATALOG_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        "Catalogue refresh complete: "
        f"{live_count}/{len(updated)} live lookups; "
        f"{len(discovered)} product URLs available."
    )


if __name__ == "__main__":
    main()
