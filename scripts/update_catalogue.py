#!/usr/bin/env python3
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "catalogue.json"

USER_AGENT = (
    "Mozilla/5.0 (compatible; SingerCustomerRecommendationBot/1.0; "
    "+https://github.com/) "
)

PRICE_RE = re.compile(r"(?:Rs\.?|LKR)\s*([0-9][0-9,]*)", re.I)
PCT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*Off", re.I)
OFFER_RE = re.compile(
    r"(?:Offer\s*-\s*|Special Discount\s*-\s*|Offers?:\s*)([^\n]+)",
    re.I,
)


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def fetch_product(sku: str, fallback: dict) -> dict:
    url = f"https://www.singersl.com/products?search={sku}"
    result = dict(fallback)
    result["url"] = url
    result["live"] = False
    result["sourceStatus"] = "Fallback retained"

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=25,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        lines = []
        for raw in soup.get_text("\n", strip=True).splitlines():
            line = clean_spaces(raw)
            if line:
                lines.append(line)
        page_text = "\n".join(lines)
        lower = page_text.lower()
        idx = lower.find(sku.lower())
        if idx < 0:
            raise ValueError("SKU not found in Singer page")

        block = page_text[max(0, idx - 1200): idx + 5000]

        result["discount"] = ""
        result["offerNote"] = ""

        price_matches = [
            int(m.group(1).replace(",", ""))
            for m in PRICE_RE.finditer(block)
            if int(m.group(1).replace(",", "")) > 0
        ]
        if price_matches:
            result["price"] = price_matches[0]
            if len(price_matches) > 1 and price_matches[1] > price_matches[0]:
                result["mrp"] = price_matches[1]

        pct = PCT_RE.search(block)
        if pct:
            number = pct.group(1).rstrip("0").rstrip(".")
            result["discount"] = f"{number}% OFF"

        offers = []
        for match in OFFER_RE.finditer(block):
            offer = clean_spaces(match.group(1))
            if offer and offer not in offers:
                offers.append(offer)
        if offers:
            result["offerNote"] = " | ".join(offers[:2])

        for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5"]):
            txt = clean_spaces(heading.get_text(" ", strip=True))
            if sku.lower() in txt.lower() and len(txt) < 220:
                result["product"] = re.sub(
                    rf"\s*[-–—]\s*{re.escape(sku)}\b", "", txt, flags=re.I
                ).strip()
                break

        result["live"] = True
        result["sourceStatus"] = "Live Singer public catalogue"
        result["liveCheckedAt"] = datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        result.pop("liveError", None)
        return result

    except Exception as exc:
        result["liveError"] = str(exc)
        return result


def main() -> None:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    products = payload.get("products", [])

    updated = [
        fetch_product(str(item.get("sku", "")), item)
        for item in products
    ]

    live_count = sum(1 for x in updated if x.get("live"))
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    out = {
        **payload,
        "schemaVersion": 1,
        "checkedAt": checked_at,
        "source": "https://www.singersl.com/products",
        "refreshMethod": "GitHub Actions scheduled scraper",
        "refreshFrequency": "hourly",
        "products": updated,
        "summary": {
            "total": len(updated),
            "liveUpdated": live_count,
            "fallbackRetained": len(updated) - live_count,
        },
        "notes": [
            "Prices and offers are read from public Singer Sri Lanka product catalogue pages.",
            "If a lookup fails, the previous saved value is retained.",
            "Stock is not used as a hard recommendation filter in the app.",
        ],
    }

    CATALOG_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Catalogue refresh complete: {live_count}/{len(updated)} live lookups.")


if __name__ == "__main__":
    main()
