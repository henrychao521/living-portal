import time
import httpx
from tdx import haversine
from config import CWA_API_KEY

_wx_cache: dict = {}
CWA_BASE = 'https://opendata.cwa.gov.tw/api/v1/rest/datastore'


async def get_weather_near(lat: float, lon: float) -> dict:
    key = f'{lat:.3f},{lon:.3f}'
    now = time.time()
    if key in _wx_cache and now < _wx_cache[key][0]:
        return _wx_cache[key][1]

    try:
        async with httpx.AsyncClient(verify=False) as c:
            r = await c.get(f'{CWA_BASE}/O-A0001-001', follow_redirects=True, params={
                'Authorization': CWA_API_KEY,
                'format': 'JSON',
                'limit': '500',
            }, timeout=15)
            r.raise_for_status()
            stations = r.json().get('records', {}).get('Station', [])

        best, best_dist = None, float('inf')
        for s in stations:
            # Use WGS84 coordinates (index 1)
            coords = s.get('GeoInfo', {}).get('Coordinates', [])
            coord = next((c for c in coords if c.get('CoordinateName') == 'WGS84'), coords[0] if coords else None)
            if not coord:
                continue
            slat = coord.get('StationLatitude')
            slon = coord.get('StationLongitude')
            if slat is None or slon is None:
                continue
            d = haversine(lat, lon, float(slat), float(slon))
            if d < best_dist:
                best_dist, best = d, s

        if not best:
            result = {'error': 'no station found'}
        else:
            obs = best.get('WeatherElement', {})
            def num(v):
                try:
                    f = float(v)
                    return None if f == -99 else f
                except (TypeError, ValueError):
                    return None

            result = {
                'station': best.get('StationName', ''),
                'distance_m': round(best_dist),
                'temperature': num(obs.get('AirTemperature')),
                'humidity': num(obs.get('RelativeHumidity')),
                'rainfall': num(obs.get('Now', {}).get('Precipitation')),
                'wind_speed': num(obs.get('WindSpeed')),
                'wind_direction': num(obs.get('WindDirection')),
                'weather_desc': obs.get('Weather') or None,
            }
    except Exception as e:
        print(f'[weather] fetch 失敗: {e}')
        result = {'error': '氣象資料暫時無法取得'}
        _wx_cache[key] = (now + 60, result)  # 錯誤只快取 1 分鐘,讓上游恢復後儘快重試
        return result

    _wx_cache[key] = (now + 600, result)
    return result
