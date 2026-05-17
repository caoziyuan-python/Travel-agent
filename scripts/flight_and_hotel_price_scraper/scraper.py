"""
Beijing flight & hotel price scraper using Ctrip.

Flights: direct API call (no browser needed).
Hotels:  headless Playwright XHR interception (no visible window).

Usage:
    python scraper.py --date 2026-05-01
    python scraper.py --date 2026-05-01 --days 14 --flights 100 --hotels 100
    python scraper.py --date 2026-05-01 --no-hotels     # flights only (no extra deps)
    python scraper.py --date 2026-05-01 --no-flights    # hotels only

Hotel scraping requires Playwright:
    pip install playwright
    playwright install chromium
"""

import argparse
import asyncio
import csv
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import requests

# Ensure UTF-8 on Windows terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

FLIGHT_CALENDAR_URL = "https://flights.ctrip.com/itinerary/api/12808/lowestPrice"

COOKIE_FILE = Path(__file__).parent / "cookie.json"

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

# Major routes to/from Beijing (IATA city code BJS covers PEK + PKX)
ROUTES = [
    ("BJS", "SHA", "北京", "上海"),
    ("BJS", "CAN", "北京", "广州"),
    ("BJS", "CTU", "北京", "成都"),
    ("BJS", "SZX", "北京", "深圳"),
    ("BJS", "WUH", "北京", "武汉"),
    ("BJS", "XIY", "北京", "西安"),
    ("BJS", "HGH", "北京", "杭州"),
    ("BJS", "CKG", "北京", "重庆"),
    ("BJS", "KMG", "北京", "昆明"),
    ("BJS", "SYX", "北京", "三亚"),
    ("BJS", "XMN", "北京", "厦门"),
    ("BJS", "NKG", "北京", "南京"),
    ("BJS", "TAO", "北京", "青岛"),
    ("BJS", "DLC", "北京", "大连"),
    ("BJS", "HRB", "北京", "哈尔滨"),
    ("SHA", "BJS", "上海", "北京"),
    ("CAN", "BJS", "广州", "北京"),
    ("CTU", "BJS", "成都", "北京"),
    ("SZX", "BJS", "深圳", "北京"),
    ("WUH", "BJS", "武汉", "北京"),
]


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    return s


def load_cookies_into_session(s: requests.Session) -> None:
    if not COOKIE_FILE.exists():
        return
    try:
        with open(COOKIE_FILE, encoding="utf-8") as f:
            for c in json.load(f):
                s.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
    except Exception:
        pass


# ── Flight scraping (direct API, no browser) ──────────────────────────────────

def fetch_flight_calendar(session: requests.Session, dep: str, arr: str) -> dict:
    """
    GET 12808 calendar API — returns ~30 days of lowest prices for one route.
    Response: {"data": {"oneWayPrice": [{YYYYMMDD: price, ...}]}}
    """
    resp = session.get(
        FLIGHT_CALENDAR_URL,
        params={
            "flightWay": "Oneway",
            "dcity": dep,
            "acity": arr,
            "direct": "false",
            "army": "false",
        },
        headers={"Referer": "https://flights.ctrip.com/online/channel/domestic"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def parse_flight_calendar(
    payload: dict,
    dep_code: str, arr_code: str,
    dep_name: str, arr_name: str,
    start_date: str, days: int,
) -> list[dict]:
    price_rows = (payload.get("data") or {}).get("oneWayPrice") or []
    if not price_rows or not isinstance(price_rows[0], dict):
        return []

    price_map: dict = price_rows[0]
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    records = []

    for i in range(days):
        dt = start_dt + timedelta(days=i)
        raw = price_map.get(dt.strftime("%Y%m%d"))
        if raw is None:
            continue
        try:
            price = int(round(float(raw)))
        except (TypeError, ValueError):
            continue
        if price > 0:
            records.append({
                "departure_city":   dep_name,
                "arrival_city":     arr_name,
                "departure_code":   dep_code,
                "arrival_code":     arr_code,
                "date":             dt.strftime("%Y-%m-%d"),
                "lowest_price_cny": price,
                "source":           "ctrip_calendar_12808",
            })
    return records


def scrape_flights(start_date: str, days: int = 30, target: int = 100) -> list[dict]:
    session = make_session()
    load_cookies_into_session(session)
    records: list[dict] = []

    for dep, arr, dep_name, arr_name in ROUTES:
        if len(records) >= target:
            break
        print(f"  {dep_name}({dep}) → {arr_name}({arr}) ...", end=" ", flush=True)
        try:
            payload = fetch_flight_calendar(session, dep, arr)
            new = parse_flight_calendar(payload, dep, arr, dep_name, arr_name, start_date, days)
            print(f"{len(new)} records")
            records.extend(new)
        except Exception as exc:
            print(f"FAILED: {exc}")
        time.sleep(1.2)

    return records[:target]


# ── Hotel scraping (headless Playwright, no visible window) ───────────────────


async def _extract_cards_from_page(page, check_in: str, check_out: str) -> list[dict]:
    """Pull hotel data from Booking.com property cards currently visible on the page."""
    import re

    records = []
    cards = await page.locator('[data-testid="property-card"]').all()
    for card in cards:
        try:
            name = ""
            name_el = card.locator('[data-testid="title"]')
            if await name_el.count():
                name = (await name_el.first.text_content() or "").strip()
            if not name:
                continue

            price_raw = ""
            price_el = card.locator('[data-testid="price-and-discounted-price"]')
            if await price_el.count():
                price_raw = (await price_el.first.text_content() or "").strip()

            score_raw = ""
            score_el = card.locator('[data-testid="review-score"] .a3b8729ab1')
            if await score_el.count():
                score_raw = (await score_el.first.text_content() or "").strip()

            addr = ""
            addr_el = card.locator('[data-testid="address"]')
            if await addr_el.count():
                addr = (await addr_el.first.text_content() or "").strip()

            dist = ""
            dist_el = card.locator('[data-testid="distance"]')
            if await dist_el.count():
                dist = (await dist_el.first.text_content() or "").strip()

            # Parse numeric price (handles "US$53", "¥389", "CNY 389", "389", etc.)
            price: float | None = None
            currency = ""
            nums = re.findall(r"[\d,]+\.?\d*", price_raw.replace(",", ""))
            if nums:
                try:
                    price = float(nums[0])
                except ValueError:
                    pass
            if "US$" in price_raw or "USD" in price_raw:
                currency = "USD"
            elif "¥" in price_raw or "CNY" in price_raw or "元" in price_raw:
                currency = "CNY"
            else:
                currency = "USD"  # Booking.com default outside CN

            score: float | None = None
            score_nums = re.findall(r"[\d.]+", score_raw)
            if score_nums:
                try:
                    score = float(score_nums[0])
                except ValueError:
                    pass

            records.append({
                "name":              name,
                "city":              "北京",
                "address":           addr,
                "distance_to_center": dist,
                "score":             score,
                "price_per_night":   price,
                "currency":          currency,
                "check_in":          check_in,
                "check_out":         check_out,
                "source":            "booking.com",
            })
        except Exception:
            continue

    return records


async def _dismiss_cookie_consent(page) -> None:
    """Click cookie-consent / privacy-notice accept buttons if present."""
    selectors = [
        '[id="onetrust-accept-btn-handler"]',
        'button[data-gdpr-consent="accept"]',
        '[data-testid="accept-button"]',
        'button:has-text("Accept")',
        'button:has-text("接受")',
        'button:has-text("同意")',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel)
            if await btn.count():
                await btn.first.click(timeout=3_000)
                await asyncio.sleep(1)
                return
        except Exception:
            continue


async def _scrape_hotels_playwright(
    check_in: str,
    check_out: str,
    target: int,
    destination: str = "Beijing, China",
) -> list[dict]:
    """Scrape Beijing hotels from Booking.com using headless Playwright (no visible window)."""
    from playwright.async_api import async_playwright

    # Primary and fallback card selectors (Booking.com occasionally changes these)
    CARD_SELECTORS = [
        '[data-testid="property-card"]',
        '[data-testid="property-card-container"]',
        'div[data-testid^="property"]',
        '.sr_property_block',
    ]

    collected: list[dict] = []
    page_size = 25  # Booking.com shows 25 results per page

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=BASE_HEADERS["User-Agent"],
            locale="zh-CN",
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()
        first_page = True

        offset = 0
        while len(collected) < target:
            url = (
                "https://www.booking.com/searchresults.zh-cn.html"
                f"?ss={quote_plus(destination)}"
                f"&checkin={check_in}&checkout={check_out}"
                f"&group_adults=1&no_rooms=1&group_children=0"
                f"&nflt=ht_id%3D204"  # hotels only
                f"&offset={offset}"
            )
            print(f"  Page {offset // page_size + 1} (offset={offset}) ...", end=" ", flush=True)

            try:
                await page.goto(url, wait_until="networkidle", timeout=60_000)
            except Exception:
                # networkidle may time out on slow pages; fall back to domcontentloaded
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    await asyncio.sleep(4)
                except Exception as exc:
                    print(f"page load failed: {exc}")
                    break

            # On the first page: dismiss cookie consent banners
            if first_page:
                await _dismiss_cookie_consent(page)
                first_page = False

            # Try each selector until one returns cards
            cards_found = False
            for sel in CARD_SELECTORS:
                try:
                    await page.locator(sel).first.wait_for(timeout=8_000)
                    cards_found = True
                    break
                except Exception:
                    continue

            if not cards_found:
                # Check whether the page looks like a bot-block / captcha
                title = await page.title()
                print(f"no cards found (page title: {title!r})")
                break

            new = await _extract_cards_from_page(page, check_in, check_out)
            if not new:
                print("no cards parsed")
                break
            print(f"{len(new)} hotels")
            collected.extend(new)
            offset += page_size
            await asyncio.sleep(2)

        await browser.close()

    return collected[:target]


def scrape_hotels(
    check_in: str,
    check_out: str | None = None,
    target: int = 100,
    destination: str = "Beijing, China",
) -> list[dict]:
    """
    Scrape Beijing hotels from Booking.com using headless Playwright.
    Hotel source is Booking.com (live scraping with Playwright).
    """
    if not check_out:
        check_out = (
            datetime.strptime(check_in, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")

    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        print(
            "  [Hotel] Playwright not installed. Run:\n"
            "    pip install playwright && playwright install chromium"
        )
        return []

    print("  (Source: Booking.com live scraping)")
    return asyncio.run(_scrape_hotels_playwright(check_in, check_out, target, destination))


# ── Output helpers ────────────────────────────────────────────────────────────

def save_csv(records: list[dict], path: Path) -> None:
    if not records:
        print(f"  (empty — skipped {path.name})")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    print(f"  → {path}  ({len(records)} rows)")


def save_json(records: list[dict], path: Path) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description=(
            "Scrape Beijing flight & hotel prices.\n"
            "Flights: Ctrip calendar API (no browser). "
            "Hotels: Booking.com via headless Playwright."
        )
    )
    p.add_argument("--date", required=True,
                   help="Start date YYYY-MM-DD (must be in the future)")
    p.add_argument("--days", type=int, default=30,
                   help="Flight calendar window in days from --date (default: 30)")
    p.add_argument("--flights", type=int, default=100,
                   help="Target flight price records (default: 100)")
    p.add_argument("--hotels", type=int, default=100,
                   help="Target hotel records (default: 100)")
    p.add_argument("--out", default="output",
                   help="Output directory (default: output/)")
    p.add_argument("--no-flights", action="store_true", help="Skip flight scraping")
    p.add_argument("--no-hotels",  action="store_true", help="Skip hotel scraping")
    return p.parse_args()


def main():
    args = parse_args()

    try:
        target_dt = datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print("Error: --date must be YYYY-MM-DD")
        return

    today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    if target_dt <= today:
        print(f"Error: --date {args.date} must be in the future (today is {today.date()})")
        return

    out = Path(args.out)
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"\n{'='*54}")
    print(f"  Ctrip Scraper  |  Target date: {args.date}")
    print(f"{'='*54}")

    if not args.no_flights:
        print(f"\n[Flights] {args.flights} records | {args.days}-day window from {args.date}")
        flights = scrape_flights(args.date, days=args.days, target=args.flights)
        print(f"[Flights] Collected: {len(flights)}")
        save_csv(flights, out / f"flights_{args.date}_{ts}.csv")
        save_json(flights, out / f"flights_{args.date}_{ts}.json")

    if not args.no_hotels:
        print(f"\n[Hotels] {args.hotels} Beijing hotels | check-in: {args.date}")
        hotels = scrape_hotels(args.date, target=args.hotels)
        print(f"[Hotels] Collected: {len(hotels)}")
        save_csv(hotels, out / f"hotels_{args.date}_{ts}.csv")
        save_json(hotels, out / f"hotels_{args.date}_{ts}.json")

    print(f"\n{'='*54}")
    print(f"  Done. Output: {out.resolve()}")
    print(f"{'='*54}\n")


if __name__ == "__main__":
    main()
