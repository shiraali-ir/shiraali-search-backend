from __future__ import annotations

import asyncio
import re
import time
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

SERVICE_NAME = "graphical"
USER_AGENT = "ShiraaliSearch/1.0 (https://shiraali.ir)"
HTTP_TIMEOUT = httpx.Timeout(18.0, connect=8.0)

app = FastAPI(title="Shiraali Graphical API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://shiraali.ir",
        "https://www.shiraali.ir",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# Cache reduces repeated external requests on Render.
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = asyncio.Lock()


async def cache_get(key: str) -> Any | None:
    async with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None

        expires, value = item

        if expires <= time.monotonic():
            _CACHE.pop(key, None)
            return None

        return value


async def cache_set(key: str, value: Any, ttl: int) -> None:
    async with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + ttl, value)


def normalized(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("ي", "ی").replace("ى", "ی")
    value = value.replace("ك", "ک").replace("‌", " ")
    return re.sub(r"\s+", " ", value)


def strip_parens(value: str) -> str:
    return normalized(re.sub(r"\s*\([^)]*\)\s*", " ", value or ""))


async def get_json(
    url: str,
    params: dict[str, Any] | None = None,
) -> Any:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        headers=headers,
        follow_redirects=True,
    ) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": SERVICE_NAME,
        "version": "1.0.0",
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": SERVICE_NAME,
    }


# ============================================================
# WEATHER
# ============================================================

WEATHER_CURRENT_FIELDS = ",".join([
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "is_day",
    "precipitation",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
])

WEATHER_DAILY_FIELDS = ",".join([
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
    "sunrise",
    "sunset",
])


@app.get("/weather")
async def weather(
    city: str = Query(..., min_length=1, max_length=100),
) -> dict[str, Any]:
    """Return current and daily weather for a city."""

    city = city.strip()

    if not city:
        raise HTTPException(
            status_code=400,
            detail="نام شهر وارد نشده است",
        )

    key = f"weather:{normalized(city)}"

    cached = await cache_get(key)

    if cached is not None:
        return cached

    # First, locate the city.
    try:
        geo = await get_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            {
                "name": city,
                "count": 8,
                "language": "en",
                "format": "json",
            },
        )

    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail="سرویس یافتن شهر پاسخ مناسبی نداد",
        ) from exc

    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail="سرویس یافتن شهر موقتاً در دسترس نیست",
        ) from exc

    candidates = geo.get("results") or []

    # Retry Persian names using Persian-language geocoding.
    if not candidates:
        try:
            geo = await get_json(
                "https://geocoding-api.open-meteo.com/v1/search",
                {
                    "name": city,
                    "count": 8,
                    "language": "fa",
                    "format": "json",
                },
            )

            candidates = geo.get("results") or []

        except httpx.HTTPError:
            candidates = []

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail="شهر یا کشور پیدا نشد",
        )

    wanted = normalized(city)

    chosen = next(
        (
            item
            for item in candidates
            if normalized(item.get("name", "")) == wanted
        ),
        candidates[0],
    )

    latitude = chosen.get("latitude")
    longitude = chosen.get("longitude")

    if latitude is None or longitude is None:
        raise HTTPException(
            status_code=502,
            detail="مختصات این مکان پیدا نشد",
        )

    place = {
        "name": chosen.get("name") or city,
        "latitude": latitude,
        "longitude": longitude,
        "country": chosen.get("country") or "",
        "countryCode": (
            chosen.get("country_code") or ""
        ).upper(),
        "timezone": chosen.get("timezone") or "",
        "isCountry": False,
        "admin1": chosen.get("admin1") or "",
    }

    # Request weather data.
    try:
        forecast = await get_json(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": latitude,
                "longitude": longitude,
                "current": WEATHER_CURRENT_FIELDS,
                "daily": WEATHER_DAILY_FIELDS,
                "forecast_days": 1,
                "timezone": "auto",
            },
        )

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise HTTPException(
                status_code=503,
                detail=(
                    "سرویس آب‌وهوا به‌دلیل محدودیت درخواست "
                    "موقتاً در دسترس نیست؛ کمی بعد دوباره امتحان کنید"
                ),
            ) from exc

        raise HTTPException(
            status_code=502,
            detail="سرویس آب‌وهوا پاسخ مناسبی نداد",
        ) from exc

    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail="سرویس آب‌وهوا موقتاً در دسترس نیست",
        ) from exc

    if not forecast.get("current"):
        raise HTTPException(
            status_code=503,
            detail="اطلاعات فعلی آب‌وهوا در دسترس نیست",
        )

    result = {
        **forecast,
        "place": place,
    }

    # Cache successful weather responses for 15 minutes.
    await cache_set(key, result, 900)

    return result


# ============================================================
# WIKIPEDIA IMAGES AND SUMMARY
# ============================================================

async def wiki_image_by_title(
    lang: str,
    title: str,
) -> str | None:
    try:
        data = await get_json(
            f"https://{lang}.wikipedia.org/w/api.php",
            {
                "action": "query",
                "titles": title,
                "redirects": 1,
                "prop": "pageimages",
                "piprop": "thumbnail",
                "pithumbsize": 900,
                "format": "json",
            },
        )

        pages = data.get("query", {}).get("pages") or {}
        page = next(iter(pages.values()), {})

        return (page.get("thumbnail") or {}).get("source")

    except httpx.HTTPError:
        return None


async def find_wiki_page(
    query: str,
) -> dict[str, Any] | None:
    q = normalized(query)

    if not q or len(q) > 50 or len(q.split()) > 4:
        return None

    key = f"wiki:{q}"

    cached = await cache_get(key)

    if cached is not None:
        return cached

    # Prefer Persian Wikipedia for Persian queries.
    if re.search(r"[\u0600-\u06ff]", q):
        languages = ["fa", "en"]
    else:
        languages = ["en", "fa"]

    found = None

    for lang in languages:
        try:
            data = await get_json(
                f"https://{lang}.wikipedia.org/w/api.php",
                {
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": query,
                    "gsrnamespace": 0,
                    "gsrlimit": 6,
                    "prop": (
                        "extracts|pageimages|pageprops|info|langlinks"
                    ),
                    "lllang": "en",
                    "exintro": 1,
                    "explaintext": 1,
                    "exlimit": "max",
                    "piprop": "thumbnail",
                    "pithumbsize": 900,
                    "ppprop": "disambiguation",
                    "inprop": "url",
                    "format": "json",
                },
            )

        except httpx.HTTPError:
            continue

        pages = sorted(
            (
                data.get("query", {}).get("pages") or {}
            ).values(),
            key=lambda item: item.get("index", 0),
        )

        for page in pages:
            title = strip_parens(page.get("title", ""))

            if not title or not (q in title or title in q):
                continue

            if (
                "disambiguation" in (
                    page.get("pageprops") or {}
                )
                or not page.get("extract")
            ):
                continue

            raw_title = page.get("title", "")
            langlinks = page.get("langlinks") or []

            found = {
                "url": (
                    page.get("fullurl")
                    or (
                        f"https://{lang}.wikipedia.org/wiki/"
                        f"{quote(raw_title.replace(' ', '_'))}"
                    )
                ),
                "title": raw_title,
                "content": (
                    page.get("extract", "")
                    .strip()
                    .split("\n")[0]
                ),
                "image": (
                    page.get("thumbnail") or {}
                ).get("source"),
                "wikidataId": (
                    page.get("pageprops") or {}
                ).get("wikibase_item"),
                "enTitle": (
                    langlinks[0].get("*")
                    if langlinks
                    else (
                        raw_title
                        if lang == "en"
                        else None
                    )
                ),
                "language": lang,
            }

            break

        if found:
            break

    if found and not found.get("image") and found.get("enTitle"):
        found["image"] = await wiki_image_by_title(
            "en",
            found["enTitle"],
        )

    await cache_set(key, found, 21600)

    return found


@app.get("/wiki")
async def wikipedia(
    query: str = Query(..., min_length=1, max_length=100),
) -> dict[str, Any]:
    page = await find_wiki_page(query)

    if not page:
        raise HTTPException(
            status_code=404,
            detail="مقالهٔ مرتبطی در ویکی‌پدیا پیدا نشد",
        )

    return page


@app.get("/wiki/image")
async def wikipedia_image(
    title: str = Query(..., min_length=1, max_length=200),
    lang: str = Query("en", pattern="^(fa|en)$"),
) -> dict[str, Any]:
    image = await wiki_image_by_title(lang, title)

    return {
        "title": title,
        "language": lang,
        "image": image,
    }


# ============================================================
# WIKIDATA COUNTRY INFORMATION
# ============================================================

async def wikidata_entity(
    qid: str,
) -> dict[str, Any] | None:
    if not re.fullmatch(r"Q\d+", qid or ""):
        return None

    key = f"wd:{qid}"

    cached = await cache_get(key)

    if cached is not None:
        return cached

    try:
        data = await get_json(
            "https://www.wikidata.org/w/api.php",
            {
                "action": "wbgetentities",
                "ids": qid,
                "props": "labels|claims|descriptions",
                "languages": "fa|en",
                "languagefallback": 1,
                "format": "json",
            },
        )

        entity = (
            data.get("entities") or {}
        ).get(qid)

        await cache_set(key, entity, 86400)

        return entity

    except httpx.HTTPError:
        return None


def entity_label(
    entity: dict[str, Any] | None,
) -> str:
    labels = (entity or {}).get("labels") or {}

    return (
        (labels.get("fa") or labels.get("en") or {})
        .get("value", "")
    )


def claim_value(
    entity: dict[str, Any],
    prop: str,
) -> Any:
    claims = (entity.get("claims") or {}).get(prop) or []

    for claim in claims:
        snak = claim.get("mainsnak") or {}

        if snak.get("snaktype") != "value":
            continue

        value = (
            (snak.get("datavalue") or {}).get("value")
        )

        if isinstance(value, dict) and value.get("id"):
            return {"id": value["id"]}

        if isinstance(value, dict) and value.get("amount") is not None:
            try:
                return float(
                    str(value["amount"]).lstrip("+")
                )
            except ValueError:
                return None

        if isinstance(value, dict) and value.get("text"):
            return value["text"]

        return value

    return None


async def search_wikidata(
    query: str,
) -> str | None:
    try:
        data = await get_json(
            "https://www.wikidata.org/w/api.php",
            {
                "action": "wbsearchentities",
                "search": query,
                "language": "fa",
                "uselang": "fa",
                "type": "item",
                "limit": 5,
                "format": "json",
            },
        )

        for item in data.get("search") or []:
            if re.fullmatch(r"Q\d+", item.get("id", "")):
                return item["id"]

    except httpx.HTTPError:
        return None

    return None


@app.get("/country-info")
async def country_info(
    query: str = Query(..., min_length=1, max_length=100),
    qid: str | None = Query(None, pattern="^Q\\d+$"),
) -> dict[str, Any]:
    entity_id = qid or await search_wikidata(query)

    if not entity_id:
        raise HTTPException(
            status_code=404,
            detail="اطلاعات این کشور در ویکی‌داده پیدا نشد",
        )

    entity = await wikidata_entity(entity_id)

    if not entity:
        raise HTTPException(
            status_code=502,
            detail="دریافت اطلاعات ویکی‌داده ناموفق بود",
        )

    claims = entity.get("claims") or {}

    fields: dict[str, Any] = {}

    properties = [
        ("P1448", "officialName"),
        ("P85", "anthem"),
        ("P36", "capital"),
        ("P1082", "population"),
        ("P38", "currency"),
        ("P6", "headOfGovernment"),
        ("P30", "continent"),
        ("P41", "flag"),
        ("P242", "locatorMap"),
    ]

    for prop, key in properties:
        fields[key] = claim_value(entity, prop)

    language_ids = []

    for claim in (claims.get("P37") or [])[:3]:
        value = (
            (claim.get("mainsnak") or {})
            .get("datavalue", {})
            .get("value") or {}
        )

        if isinstance(value, dict) and value.get("id"):
            language_ids.append(value["id"])

    fields["officialLanguages"] = language_ids

    ids: set[str] = set(language_ids)

    for value in fields.values():
        if isinstance(value, dict) and value.get("id"):
            ids.add(value["id"])

    labels: dict[str, str] = {}

    for ref_id in ids:
        ref_entity = await wikidata_entity(ref_id)
        label = entity_label(ref_entity)

        if label:
            labels[ref_id] = label

    for key, value in list(fields.items()):
        if isinstance(value, dict) and value.get("id"):
            fields[key] = {
                "id": value["id"],
                "label": labels.get(value["id"], ""),
            }

    fields["officialLanguages"] = [
        {
            "id": ref_id,
            "label": labels.get(ref_id, ""),
        }
        for ref_id in language_ids
    ]

    for image_key in ("flag", "locatorMap"):
        value = fields.get(image_key)

        if isinstance(value, str) and value:
            fields[image_key + "Url"] = (
                "https://commons.wikimedia.org/wiki/Special:FilePath/"
                + quote(value, safe="")
            )
        else:
            fields[image_key + "Url"] = None

    title = entity_label(entity) or query

    if not isinstance(fields.get("officialName"), str):
        fields["officialName"] = title

    fields["qid"] = entity_id
    fields["title"] = title
    fields["sourceUrl"] = (
        f"https://www.wikidata.org/wiki/{entity_id}"
    )

    return fields


# ============================================================
# LOCAL TIME AND TIME ZONES
# ============================================================

CAPITAL_TZ = {
    "IR": "Asia/Tehran",
    "GB": "Europe/London",
    "JP": "Asia/Tokyo",
    "US": "America/New_York",
    "FR": "Europe/Paris",
    "DE": "Europe/Berlin",
    "TR": "Europe/Istanbul",
    "IQ": "Asia/Baghdad",
    "AF": "Asia/Kabul",
    "SA": "Asia/Riyadh",
    "AE": "Asia/Dubai",
    "IN": "Asia/Kolkata",
    "RU": "Europe/Moscow",
    "CN": "Asia/Shanghai",
    "CA": "America/Toronto",
    "AU": "Australia/Sydney",
    "IT": "Europe/Rome",
    "ES": "Europe/Madrid",
}

WEEKENDS = {
    "IR": [5],
    "AF": [5],
    "SA": [5, 6],
    "EG": [5, 6],
    "IQ": [5, 6],
    "KW": [5, 6],
    "QA": [5, 6],
    "JO": [5, 6],
    "DZ": [5, 6],
    "IL": [5, 6],
}

IR_SOLAR = [
    (1, 1, "نوروز", True),
    (1, 2, "نوروز", True),
    (1, 3, "نوروز", True),
    (1, 4, "نوروز", True),
    (1, 12, "روز جمهوری اسلامی", True),
    (1, 13, "سیزده‌به‌در", True),
    (3, 14, "رحلت امام خمینی", True),
    (3, 15, "قیام ۱۵ خرداد", True),
    (11, 22, "پیروزی انقلاب اسلامی", True),
    (12, 29, "ملی شدن صنعت نفت", True),
]

IR_HIJRI = [
    (1, 9, "تاسوعا", True),
    (1, 10, "عاشورا", True),
    (2, 20, "اربعین", True),
    (2, 28, "رحلت پیامبر و شهادت امام حسن", True),
    (2, 30, "شهادت امام رضا", True),
    (3, 17, "ولادت پیامبر و امام صادق", True),
    (6, 3, "شهادت حضرت فاطمه", True),
    (7, 13, "ولادت امام علی", True),
    (7, 27, "مبعث", True),
    (8, 15, "ولادت امام زمان", True),
    (9, 21, "شهادت امام علی", True),
    (10, 1, "عید فطر", True),
    (10, 2, "عید فطر", True),
    (10, 25, "شهادت امام صادق", True),
    (12, 10, "عید قربان", True),
    (12, 18, "عید غدیر", True),
]


@app.get("/time")
async def local_time(
    timezone_name: str = Query(
        ...,
        alias="timezone",
        min_length=1,
        max_length=80,
    ),
    country_code: str = Query("", max_length=2),
) -> dict[str, Any]:
    cc = country_code.upper()
    tz = CAPITAL_TZ.get(cc, timezone_name)

    try:
        zone = ZoneInfo(tz)

    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=400,
            detail="منطقهٔ زمانی معتبر نیست",
        ) from exc

    now = datetime.now(zone)

    return {
        "timezone": tz,
        "localTime": now.isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "date": now.date().isoformat(),
        "weekday": now.strftime("%A"),
        "utcOffset": now.strftime("%z"),
    }


# ============================================================
# PUBLIC HOLIDAYS
# ============================================================

@app.get("/holidays")
async def holidays(
    country_code: str = Query(..., min_length=2, max_length=2),
    year: int = Query(
        default_factory=lambda: datetime.now(timezone.utc).year,
        ge=1900,
        le=2200,
    ),
    on_date: date | None = Query(None, alias="date"),
) -> dict[str, Any]:
    cc = country_code.upper()
    target_date = on_date or date.today()

    cache_key = f"holidays:{cc}:{year}"

    if cc == "IR":
        try:
            today = datetime.now(ZoneInfo("Asia/Tehran"))

        except ZoneInfoNotFoundError:
            today = datetime.now()

        events = []

        # Persian calendar conversion is optional.
        try:
            import jdatetime

            solar = jdatetime.date.fromgregorian(
                date=today.date()
            )

            for month, day, name, _holiday in IR_SOLAR:
                if (month, day) == (solar.month, solar.day):
                    events.append({
                        "name": name,
                        "date": today.date().isoformat(),
                        "type": "iranian-solar",
                    })

        except ImportError:
            pass

        weekday = (today.weekday() + 1) % 7

        return {
            "countryCode": cc,
            "year": year,
            "date": today.date().isoformat(),
            "timezone": "Asia/Tehran",
            "isWeekend": weekday in WEEKENDS["IR"],
            "events": events,
            "note": (
                "برای تبدیل دقیق تقویم شمسی به jdatetime نیاز است؛ "
                "تقویم اصلی سمت مرورگر هم حفظ شود."
            ),
        }

    cached = await cache_get(cache_key)

    if cached is None:
        try:
            cached = await get_json(
                f"https://date.nager.at/api/v3/PublicHolidays/{year}/{cc}"
            )

            if not isinstance(cached, list):
                cached = []

            await cache_set(cache_key, cached, 86400)

        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503,
                detail="سرویس تعطیلات رسمی موقتاً در دسترس نیست",
            ) from exc

    iso = target_date.isoformat()

    events = [
        {
            "name": item.get("name"),
            "localName": item.get("localName"),
            "date": item.get("date"),
            "type": "public-holiday",
        }
        for item in cached
        if item.get("date") == iso and not item.get("counties")
    ]

    weekday = (target_date.weekday() + 1) % 7

    return {
        "countryCode": cc,
        "year": year,
        "date": iso,
        "isWeekend": weekday in WEEKENDS.get(cc, [0, 6]),
        "events": events,
    }
