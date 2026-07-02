import time
import httpx
from tdx import haversine

from typing import Optional, Tuple
_cam_cache: Optional[Tuple[float, list]] = None
CAM_LIST_URL = 'https://www.twipcam.com/api/v1/cam-list.json'

async def _get_all_cameras() -> list:
    global _cam_cache
    now = time.time()
    if _cam_cache and now < _cam_cache[0]:
        return _cam_cache[1]
    async with httpx.AsyncClient() as c:
        r = await c.get(CAM_LIST_URL, timeout=30)
        r.raise_for_status()
        cams = r.json()
    _cam_cache = (now + 3600, cams)
    return cams


async def get_nearby_cameras(lat: float, lon: float, radius_m: float = 1000, limit: int = 5) -> list:
    cams = await _get_all_cameras()
    nearby = []
    for cam in cams:
        try:
            dist = haversine(lat, lon, float(cam['lat']), float(cam['lon']))
            if dist <= radius_m:
                nearby.append({
                    'id': cam.get('id'),
                    'name': cam.get('name', ''),
                    'lat': cam['lat'],
                    'lon': cam['lon'],
                    'cam_url': cam.get('cam_url', ''),
                    'distance_m': round(dist),
                })
        except (KeyError, TypeError, ValueError):
            continue
    nearby.sort(key=lambda x: x['distance_m'])
    return nearby[:limit]
