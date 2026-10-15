from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
app = FastAPI(title="Shiraali Graphical API")
app.add_middleware(CORSMiddleware, allow_origins=["https://shiraali.ir", "http://localhost:8000", "http://127.0.0.1:8000"], allow_methods=["GET"], allow_headers=["*"])
TIMEOUT = httpx.Timeout(12.0, connect=5.0)
@app.get("/health")
def health(): return {"ok": True, "service": "graphical"}
@app.get("/weather")
async def weather(city: str = Query(..., min_length=1, max_length=100), lang: str = "fa"):
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as c:
            g = await c.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1, "language": "fa" if lang == "fa" else "en", "format": "json"})
            g.raise_for_status(); places = g.json().get("results") or []
            if not places: raise HTTPException(status_code=404, detail="شهر پیدا نشد")
            p = places[0]
            w = await c.get("https://api.open-meteo.com/v1/forecast", params={"latitude": p["latitude"], "longitude": p["longitude"], "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m", "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset", "timezone": "auto", "forecast_days": 1})
            w.raise_for_status()
            return {"place": {"name": p.get("name"), "country": p.get("country"), "latitude": p["latitude"], "longitude": p["longitude"], "timezone": w.json().get("timezone")}, "weather": w.json()}
    except HTTPException: raise
    except httpx.HTTPError as e: raise HTTPException(status_code=502, detail="سرویس آب‌وهوا موقتاً در دسترس نیست") from e
