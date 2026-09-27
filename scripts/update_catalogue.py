#!/usr/bin/env python3
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
CATALOG_PATH=ROOT/"catalogue.json"
BASE_URL="https://www.singersl.com"
USER_AGENT="Mozilla/5.0 (compatible; SingerCustomerRecommendationBot/3.0; +https://github.com/)"
LISTING_URLS=[f"{BASE_URL}/products?listview=true&order_by=nf&page={page}" for page in range(1,61)]
PCT_RE=re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*Off",re.I)
OFFER_RE=re.compile(r"(?:Today's Sale\s*[-:]\s*)?(?:Offer\s*[-:]\s*|Special Discount\s*[-:]\s*|Offers?\s*[-:]\s*)([^\n]+)",re.I)
BRANDS=["Singer","Samsung","Sony","Whirlpool","Beko","Sisil","ASUS","LG","Philips","Panasonic","Dell","HP","Acer","Apple","Oppo","Vivo","Redmi","Realme","Haier","TCL","Hitachi"]

def clean_spaces(value:str)->str:
    return re.sub(r"\s+"," ",value or "").strip()

def now_iso()->str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def get(url:str,timeout:int=25):
    return requests.get(url,headers={"User-Agent":USER_AGENT,"Accept-Language":"en-US,en;q=0.9","Accept":"text/html,application/xhtml+xml,application/xml;q=0.8,*/*;q=0.7"},timeout=timeout)

def infer_category(text:str)->str:
    s=(text or "").lower()
    rules=[
        ("Television",r"\btv\b|television|led tv|smart tv|google tv"),
        ("Refrigerator",r"refrigerator|fridge|freezer"),
        ("Washing Machine",r"washing|washer|wash machine"),
        ("Air Conditioner",r"air conditioner|\ba/c\b|btu"),
        ("Rice Cooker",r"rice cooker"),
        ("Kitchen Appliance",r"blender|grinder|mixer|kettle|oven|microwave|hood|toaster|air fryer|induction|cooker|hob"),
        ("Audio",r"speaker|soundbar|home theatre|theatre bar|audio|sound system"),
        ("Mobile",r"mobile|smartphone|iphone|galaxy|oppo|vivo|redmi|realme"),
        ("Computer",r"laptop|notebook|computer|desktop|printer|tablet|ipad|asus|acer|dell|\bhp\b"),
        ("Furniture",r"sofa|table|chair|bed|mattress|wardrobe|cupboard|dressing|cabinet|furniture"),
        ("Water Pump",r"water pump|pump"),
        ("Sewing Machine",r"sewing machine|sewing"),
        ("Personal Care",r"trimmer|shaver|hair dryer|hair care|fitness|massager"),
        ("Fan",r"\bfan\b|ceiling fan|pedestal fan|stand fan"),
        ("Iron",r"\biron\b|steam iron"),
    ]
    for category,pattern in rules:
        if re.search(pattern,s,re.I):
            return category
    return "Other"

def numbers_from_value(value):
    if isinstance(value,(int,float)):
        return float(value)
    if isinstance(value,str):
        m=re.search(r"[0-9][0-9,]*(?:\.[0-9]+)?",value.replace(" ",""))
        if m:
            return float(m.group(0).replace(",",""))
    return None

def parse_jsonld(soup:BeautifulSoup)->list:
    out=[]
    for tag in soup.find_all("script",type="application/ld+json"):
        raw=tag.string or tag.get_text(strip=True)
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out

def iter_json_objects(obj):
    if isinstance(obj,list):
        for x in obj:
            yield from iter_json_objects(x)
    elif isinstance(obj,dict):
        yield obj
        for value in obj.values():
            if isinstance(value,(dict,list)):
                yield from iter_json_objects(value)

def extract_product_from_page(url:str,html:str):
    soup=BeautifulSoup(html,"html.parser")
    lines=[clean_spaces(x) for x in soup.get_text("\n",strip=True).splitlines() if clean_spaces(x)]
    page_text="\n".join(lines)
    product_obj=None
    for data in parse_jsonld(soup):
        for obj in iter_json_objects(data):
            typ=obj.get("@type")
            types=typ if isinstance(typ,list) else [typ]
            if any(str(t).lower()=="product" for t in types if t):
                product_obj=obj
                break
        if product_obj:
            break
    title=clean_spaces(product_obj.get("name","")) if product_obj else ""
    if not title:
        h1=soup.find("h1")
        title=clean_spaces(h1.get_text(" ",strip=True)) if h1 else ""
    if not title and soup.title:
        title=clean_spaces(soup.title.get_text(" ",strip=True))
    sku=""
    if product_obj:
        sku=clean_spaces(str(product_obj.get("sku") or product_obj.get("mpn") or product_obj.get("productID") or ""))
    offers=product_obj.get("offers") if product_obj else None
    if isinstance(offers,list):
        offers=offers[0] if offers else {}
    if not isinstance(offers,dict):
        offers={}
    price=numbers_from_value(offers.get("price"))
    if price is None:
        price_tag=soup.find("meta",attrs={"property":"product:price:amount"}) or soup.find("meta",attrs={"name":"product:price:amount"})
        if price_tag:
            price=numbers_from_value(price_tag.get("content"))
    mrp=None
    high=numbers_from_value(offers.get("highPrice"))
    if high is not None and price is not None and high>price:
        mrp=high
    discount=""
    pct=PCT_RE.search(page_text)
    if pct:
        number=pct.group(1).rstrip("0").rstrip(".")
        discount=f"{number}% OFF"
    offers_found=[]
    for match in OFFER_RE.finditer(page_text):
        offer=clean_spaces(match.group(1))
        if offer and offer not in offers_found:
            offers_found.append(offer)
    availability=str(offers.get("availability") or "").lower()
    in_stock=None
    if "instock" in availability or "limitedavailability" in availability:
        in_stock=True
    elif "outofstock" in availability or "soldout" in availability:
        in_stock=False
    elif re.search(r"\bout of stock\b|\bsold out\b",page_text,re.I):
        in_stock=False
    if not sku:
        slug=urlparse(url).path.rstrip("/").split("/")[-1]
        sku=re.sub(r"[^A-Za-z0-9]+","-",slug).strip("-").upper()[:80]
    if not title or price is None:
        return None
    brand=""
    if product_obj:
        raw_brand=product_obj.get("brand")
        if isinstance(raw_brand,dict):
            brand=clean_spaces(str(raw_brand.get("name") or ""))
        else:
            brand=clean_spaces(str(raw_brand or ""))
    if not brand:
        for b in BRANDS:
            if re.search(rf"\b{re.escape(b)}\b",title,re.I):
                brand=b
                break
    category=clean_spaces(str((product_obj or {}).get("category") or "")) or infer_category(title)
    return {
        "category":category,"product":title,"sku":sku,"price":int(round(price)),"url":url,
        "mrp":int(round(mrp)) if mrp is not None else None,"discount":discount,
        "offerNote":" | ".join(offers_found[:3]),"brand":brand,"inStock":in_stock,
        "live":True,"sourceStatus":"Live Singer product page","liveCheckedAt":now_iso()
    }

def discover_product_urls()->set[str]:
    urls=set()
    def fetch_listing(url):
        try:
            r=get(url,20);r.raise_for_status();return r.text
        except Exception:
            return ""
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures=[pool.submit(fetch_listing,url) for url in LISTING_URLS]
        for future in as_completed(futures):
            html=future.result()
            if not html:
                continue
            soup=BeautifulSoup(html,"html.parser")
            for anchor in soup.find_all("a",href=True):
                absolute=urljoin(BASE_URL,anchor.get("href","").strip())
                if urlparse(absolute).path.lower().startswith("/product/"):
                    urls.add(absolute.split("#",1)[0])
    return urls

def seed_existing_urls(payload:dict)->set[str]:
    return {str(x.get("url","")).strip().split("#",1)[0] for x in payload.get("products",[]) if "/product/" in str(x.get("url","")).lower()}

def parse_urls(urls:set[str],fallback_by_url:dict[str,dict])->list[dict]:
    def fetch_one(url):
        try:
            r=get(url,25);r.raise_for_status();return url,r.text
        except Exception:
            return url,""
    results=[]
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures=[pool.submit(fetch_one,u) for u in sorted(urls)]
        for future in as_completed(futures):
            url,html=future.result()
            if not html:
                if url in fallback_by_url:
                    row=dict(fallback_by_url[url]);row["live"]=False;row["sourceStatus"]="Fallback retained";row["liveError"]="Product page request failed";results.append(row)
                continue
            parsed=extract_product_from_page(url,html)
            if parsed:
                results.append(parsed)
            elif url in fallback_by_url:
                row=dict(fallback_by_url[url]);row["live"]=False;row["sourceStatus"]="Fallback retained";row["liveError"]="Product page parse failed";results.append(row)
    return results

def fallback_products(payload:dict)->list[dict]:
    out=[]
    for item in payload.get("products",[]):
        if item.get("product"):
            row=dict(item);row.setdefault("live",False);row.setdefault("sourceStatus","Fallback retained");out.append(row)
    return out

def health_guard(previous,parsed,discovered):
    previous_live=sum(1 for x in previous if x.get("live"))
    new_live=sum(1 for x in parsed if x.get("live"))
    if previous_live>=50 and new_live<max(50,int(previous_live*0.60)):
        raise RuntimeError(f"Catalogue health guard stopped refresh: previous live={previous_live}, new live={new_live}, discovered={len(discovered)}.")
    if previous_live>=50 and len(discovered)<100:
        raise RuntimeError(f"Catalogue discovery health guard stopped refresh: only {len(discovered)} product URLs discovered.")

def main():
    payload=json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    existing=fallback_products(payload)
    fallback_by_url={str(x.get("url","")).strip():x for x in existing if str(x.get("url","")).strip()}
    discovered=discover_product_urls()
    urls=discovered|seed_existing_urls(payload)
    parsed=parse_urls(urls,fallback_by_url)
    health_guard(existing,parsed,discovered)
    merged={}
    for item in existing+parsed:
        sku=clean_spaces(str(item.get("sku",""))).upper()
        url=clean_spaces(str(item.get("url","")))
        key=sku or url
        if not key:
            continue
        current=merged.get(key)
        if current is None or item.get("live") or not current.get("live"):
            merged[key]=item
    products=sorted(merged.values(),key=lambda x:(str(x.get("category","Other")),str(x.get("product","")),str(x.get("sku",""))))
    live_count=sum(1 for x in products if x.get("live"))
    stock_known=sum(1 for x in products if x.get("live") and x.get("inStock") is not None)
    in_stock=sum(1 for x in products if x.get("live") and x.get("inStock") is True)
    sku_values=[str(x.get("sku","")).strip().upper() for x in products if str(x.get("sku","")).strip()]
    duplicate_skus=len(products)-len(set(sku_values))
    checked_at=now_iso()
    out={**payload,"schemaVersion":3,"checkedAt":checked_at,"source":f"{BASE_URL}/products","refreshMethod":"Dynamic public catalogue discovery + exact product pages + JSON-LD/structured ecommerce data","refreshFrequency":"hourly","products":products,"summary":{"total":len(products),"liveUpdated":live_count,"fallbackRetained":len([x for x in products if not x.get("live")]),"productUrlsDiscovered":len(discovered),"stockStatusKnown":stock_known,"inStock":in_stock,"duplicateSkus":duplicate_skus,"missingPrice":sum(1 for x in products if not x.get("price")),"missingUrl":sum(1 for x in products if not x.get("url")),"mappedCategories":sum(1 for x in products if x.get("category") and x.get("category")!="Other")},"notes":[
        "The updater discovers public Singer product pages from catalogue listing pages and supplements them with previously saved product URLs.",
        "Product category and brand use structured ecommerce data when present, with keyword fallback.",
        "Current price, MRP, discount and visible offers are read from public product page data where available.",
        "Internal stock availability is used only for sales-team recommendation eligibility and is never included in customer-facing messages.",
        "A health guard stops replacement when live catalogue coverage drops abnormally so a source-site failure cannot wipe a good snapshot.",
        "If a specific product lookup fails, the previous saved value is retained where possible."
    ]}
    CATALOG_PATH.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Catalogue refresh complete: {live_count}/{len(products)} live products; {len(discovered)} URLs discovered; {in_stock} in stock where status was available; {duplicate_skus} duplicate SKUs.")

if __name__=="__main__":
    main()
