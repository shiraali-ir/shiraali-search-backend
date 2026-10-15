
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx

app = FastAPI(title="Shiraali Graphical API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://shiraali.ir",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

TIMEOUT = httpx.Timeout(25.0, connect=10.0)


@app.get("/health")
def health():
    return {"ok": True, "service": "graphical"}


@app.get("/weather")
async def weather(
    city: str = Query(..., min_length=1, max_length=100),
    lang: str = "fa",
):
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            follow_redirects=True,
        ) as client:

            geocoding = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": city,
                    "count": 1,
                    "language": "fa" if lang == "fa" else "en",
                    "format": "json",
                },
            )
            geocoding.raise_for_status()

            places = geocoding.json().get("results") or []

            if not places:
                raise HTTPException(
                    status_code=404,
                    detail="شهر پیدا نشد",
                )

            place = places[0]

            response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": (
                        "temperature_2m,relative_humidity_2m,"
                        "apparent_temperature,is_day,precipitation,"
                        "weather_code,wind_speed_10m"
                    ),
                    "daily": (
                        "weather_code,temperature_2m_max,"
                        "temperature_2m_min,sunrise,sunset"
                    ),
                    "timezone": "auto",
                    "forecast_days": 1,
                },
            )
            response.raise_for_status()

            weather_data = response.json()

            return {
                "place": {
                    "name": place.get("name"),
                    "country": place.get("country"),
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "timezone": weather_data.get("timezone"),
                },
                "weather": weather_data,
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
```
