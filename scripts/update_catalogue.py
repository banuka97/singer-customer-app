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

LISTING_URLS = [
    f"{BASE_URL}/products?listview=true&order_by=nf&page={page}"
    for page in range(1, 16)
]


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get(url: str, timeout: int = 25):
    return requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
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


def extract_values(text: str, result: dict) -> None:
    prices = [
        int(m.group(1).replace(",", ""))
        for m in PRICE_RE.finditer(text)
        if int(m.group(1).replace(",", "")) > 0
    ]
    if prices:
        result["price"] = prices[0]
        higher = [p for p in prices[1:] if p > prices[0]]
        if higher:
            result["mrp"] = min(higher)

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
    found: dict[str, str] = {}

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

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_listing, url) for url in LISTING_URLS]
        for future in as_completed(futures):
            html = future.result()
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            anchors = soup.find_all("a", href=True)

            for sku in list(wanted_skus - set(found)):
                sku_low = sku.lower()
                for anchor in anchors:
                    href = anchor.get("href", "")
                    label = clean_spaces(anchor.get_text(" ", strip=True))
                    if sku_low in href.lower() or sku_low in label.lower():
                        if "/product/" in href.lower():
                            found[sku] = urljoin(BASE_URL, href)
                            break

                if sku in found:
                    continue

                # Fallback: locate SKU text inside a product card and then its nearest product link.
                for node in soup.find_all(string=re.compile(re.escape(sku), re.I)):
                    parent = node.parent
                    for ancestor in [parent] + list(parent.parents)[:5]:
                        link = ancestor.find("a", href=True)
                        if link and "/product/" in link.get("href", "").lower():
                            found[sku] = urljoin(BASE_URL, link["href"])
                            break
                    if sku in found:
                        break

            if len(found) == len(wanted_skus):
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

        # Require the actual product code, not just a generic search page.
        if sku.lower() not in page_text.lower():
            raise ValueError("SKU not found on Singer product page")

        extract_values(page_text, result)

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


def parse_listing_fallback(sku: str, html: str, fallback: dict) -> dict:
    result = dict(fallback)
    result["live"] = False
    result["sourceStatus"] = "Fallback retained"

    soup, lines = page_lines(html)
    page_text = "\n".join(lines)
    idx = page_text.lower().find(sku.lower())
    if idx < 0:
        return result

    block = page_text[max(0, idx - 500): idx + 1800]
    extract_values(block, result)

    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5"]):
        txt = clean_spaces(heading.get_text(" ", strip=True))
        if sku.lower() in txt.lower() and len(txt) < 260:
            result["product"] = re.sub(
                rf"\s*[-–—]\s*{re.escape(sku)}\b", "", txt, flags=re.I
            ).strip()
            break

    result["live"] = True
    result["sourceStatus"] = "Live Singer public catalogue listing"
    result["liveCheckedAt"] = now_iso()
    result.pop("liveError", None)
    return result


def main() -> None:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    products = payload.get("products", [])

    wanted = {
        str(item.get("sku", "")).strip()
        for item in products
        if str(item.get("sku", "")).strip() and str(item.get("sku", "")).strip().upper() != "CATALOGUE"
    }

    session = requests.Session()
    discovered = discover_product_urls(session, wanted)

    # Fetch exact product pages concurrently.
    updated_by_sku: dict[str, dict] = {}

    def fetch_one(item: dict) -> tuple[str, dict]:
        sku = str(item.get("sku", "")).strip()
        if not sku or sku.upper() == "CATALOGUE":
            result = dict(item)
            result["live"] = False
            result["sourceStatus"] = "Fallback retained (placeholder SKU)"
            return sku, result

        product_url = discovered.get(sku)
        if product_url:
            result = parse_product_page(sku, product_url, item)
            if result.get("live"):
                return sku, result

        # Final fallback: the original search endpoint, in case the listing crawler missed it.
        search_url = f"{BASE_URL}/products?search={sku}"
        result = dict(item)
        result["url"] = product_url or search_url
        result["live"] = False
        result["sourceStatus"] = "Fallback retained"
        try:
            resp = get(search_url, timeout=20)
            resp.raise_for_status()
            result = parse_listing_fallback(sku, resp.text, result)
            if result.get("live"):
                return sku, result
        except Exception as exc:
            result["liveError"] = str(exc)

        if not product_url:
            result.setdefault("liveError", "Product URL not discovered in Singer catalogue")
        return sku, result

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_one, item) for item in products]
        for future in as_completed(futures):
            sku, result = future.result()
            updated_by_sku[sku] = result

    updated = [updated_by_sku.get(str(item.get("sku", "")).strip(), item) for item in products]

    live_count = sum(1 for x in updated if x.get("live") and str(x.get("sku", "")).upper() != "CATALOGUE")
    checked_at = now_iso()

    out = {
        **payload,
        "schemaVersion": 1,
        "checkedAt": checked_at,
        "source": f"{BASE_URL}/products",
        "refreshMethod": "GitHub Actions catalogue crawl + exact product pages",
        "refreshFrequency": "hourly",
        "products": updated,
        "summary": {
            "total": len(updated),
            "liveUpdated": live_count,
            "fallbackRetained": len(updated) - live_count,
            "productUrlsDiscovered": len(discovered),
        },
        "notes": [
            "The updater first crawls public Singer catalogue pages to discover real product URLs by SKU.",
            "It then reads the exact product page for current price, MRP, discount and visible offers.",
            "If an exact lookup fails, the previous saved value is retained.",
            "The placeholder SKU CATALOGUE is never counted as a live product.",
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
        f"{len(discovered)} product URLs discovered."
    )


if __name__ == "__main__":
    main()
