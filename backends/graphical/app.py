from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx

app = FastAPI(title="Shiraali Graphical API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://shiraali.ir",
        "https://www.shiraali.ir",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

TIMEOUT = httpx.Timeout(25.0, connect=10.0)


@app.get("/")
def root():
    return {"ok": True, "service": "shiraali-graphical"}


@app.get("/health")
def health():
    return {"ok": True, "service": "graphical"}


@app.get("/weather")
async def weather(
    city: str = Query(..., min_length=1, max_length=100),
    lang: str = "fa",
):
    city = city.strip()

    if not city:
        raise HTTPException(
            status_code=400,
            detail="نام شهر را وارد کنید",
        )

    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            follow_redirects=True,
        ) as client:
            geo_response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": city,
                    "count": 1,
                    "language": "fa" if lang == "fa" else "en",
                    "format": "json",
                },
            )
            geo_response.raise_for_status()

            places = geo_response.json().get("results") or []

            if not places:
                raise HTTPException(
                    status_code=404,
                    detail="شهر پیدا نشد",
                )

            place = places[0]

            weather_response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": (
                        "temperature_2m,"
                        "relative_humidity_2m,"
                        "apparent_temperature,"
                        "is_day,"
                        "precipitation,"
                        "weather_code,"
                        "wind_speed_10m"
                    ),
                    "daily": (
                        "weather_code,"
                        "temperature_2m_max,"
                        "temperature_2m_min,"
                        "sunrise,"
                        "sunset"
                    ),
                    "timezone": "auto",
                    "forecast_days": 1,
                },
            )
            weather_response.raise_for_status()

            data = weather_response.json()

            return {
                "place": {
                    "name": place.get("name"),
                    "country": place.get("country"),
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "timezone": data.get("timezone"),
                },
                "weather": data,
            }

    except HTTPException:
        raise

    except httpx.HTTPError as error:
        print(
            f"Weather API error: "
            f"{type(error).__name__}: {error}"
        )
        raise HTTPException(
            status_code=502,
            detail="سرویس آب‌وهوا موقتاً در دسترس نیست",
        ) from error
