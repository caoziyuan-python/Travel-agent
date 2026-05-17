# Beijing Flight & Hotel Price Scraper

Scrapes flight and hotel prices for Beijing from two sources:

| Data | Source | Method |
|------|--------|--------|
| **Flights** | Ctrip (携程) | Direct API — no browser needed |
| **Hotels** | Booking.com | Headless browser (no visible window) |

> Ctrip's hotel search requires login in a browser, so Booking.com is used instead.  
> Flight prices are in **CNY**. Hotel prices are in **USD**.

---

## Setup

```bash
pip install requests playwright
playwright install chromium
```

---

## Usage

```bash
python scraper.py --date YYYY-MM-DD
```

The date must be in the future (greater than today).

### Examples

```bash
# Flights + hotels for April 30
python scraper.py --date 2026-04-30

# Flights only (no Playwright needed)
python scraper.py --date 2026-04-30 --no-hotels

# Hotels only
python scraper.py --date 2026-04-30 --no-flights

# Custom counts and window
python scraper.py --date 2026-04-30 --flights 200 --hotels 150 --days 60

# Custom output folder
python scraper.py --date 2026-04-30 --out data/
```

### All options

| Flag | Default | Description |
|------|---------|-------------|
| `--date` | *(required)* | Target date `YYYY-MM-DD`, must be in the future |
| `--days` | `30` | How many days of flight calendar to collect starting from `--date` |
| `--flights` | `100` | Target number of flight price records |
| `--hotels` | `100` | Target number of hotel records |
| `--out` | `output/` | Output directory |
| `--no-flights` | — | Skip flight scraping |
| `--no-hotels` | — | Skip hotel scraping (no Playwright needed) |

---

## Output

Files are saved to `output/` (created automatically):

```
output/
  flights_2026-04-30_HHMMSS.csv
  flights_2026-04-30_HHMMSS.json
  hotels_2026-04-30_HHMMSS.csv
  hotels_2026-04-30_HHMMSS.json
```

### Flight columns

| Column | Description |
|--------|-------------|
| `departure_city` | Departure city name (Chinese) |
| `arrival_city` | Arrival city name (Chinese) |
| `departure_code` | IATA city code |
| `arrival_code` | IATA city code |
| `date` | Flight date |
| `lowest_price_cny` | Lowest one-way fare in CNY |
| `source` | `ctrip_calendar_12808` |

### Hotel columns

| Column | Description |
|--------|-------------|
| `name` | Hotel name |
| `city` | `北京` |
| `address` | Street address (if available) |
| `distance_to_center` | Distance from city center |
| `score` | Guest review score |
| `price_per_night` | Price per night |
| `currency` | `USD` |
| `check_in` | Check-in date |
| `check_out` | Check-out date (always next day) |
| `source` | `booking.com` |

---

## Flight routes covered

Beijing (BJS) departures to: Shanghai, Guangzhou, Chengdu, Shenzhen, Wuhan, Xi'an, Hangzhou, Chongqing, Kunming, Sanya, Xiamen, Nanjing, Qingdao, Dalian, Harbin

Return routes (→ Beijing) from: Shanghai, Guangzhou, Chengdu, Shenzhen, Wuhan
