import time
import math
import asyncio
from typing import Any, Optional
import httpx
from config import TDX_CLIENT_ID, TDX_CLIENT_SECRET, TDX_TOKEN_URL, TDX_BASE

# ── Token cache ──────────────────────────────────────────────────────────────

_token: Optional[str] = None
_token_exp: float = 0
_token_lock = asyncio.Lock()

async def _get_token() -> str:
    global _token, _token_exp
    async with _token_lock:
        if time.time() < _token_exp - 300:
            return _token
        async with httpx.AsyncClient() as c:
            r = await c.post(TDX_TOKEN_URL, data={
                'grant_type': 'client_credentials',
                'client_id': TDX_CLIENT_ID,
                'client_secret': TDX_CLIENT_SECRET,
            }, timeout=10)
            r.raise_for_status()
            d = r.json()
            _token = d['access_token']
            _token_exp = time.time() + d['expires_in']
    return _token


async def _tdx_get(path: str, params: Optional[dict] = None) -> Any:
    token = await _get_token()
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept-Encoding': 'gzip',
    }
    async with httpx.AsyncClient() as c:
        r = await c.get(f'{TDX_BASE}{path}', headers=headers,
                        params={'$format': 'JSON', **(params or {})}, timeout=20)
        r.raise_for_status()
        return r.json()


# ── In-memory cache helper ────────────────────────────────────────────────────

_cache: dict[str, tuple[float, Any]] = {}

async def _cached(key: str, ttl: float, fn) -> Any:
    now = time.time()
    if key in _cache and now < _cache[key][0]:
        return _cache[key][1]
    data = await fn()
    _cache[key] = (now + ttl, data)
    return data


# ── TRA endpoints ─────────────────────────────────────────────────────────────

async def get_tra_stations() -> list:
    async def fetch():
        d = await _tdx_get('/v3/Rail/TRA/Station')
        return d.get('Stations', [])
    return await _cached('tra_stations', 86400, fetch)


async def get_train_liveboard() -> list:
    async def fetch():
        d = await _tdx_get('/v3/Rail/TRA/TrainLiveBoard')
        return d.get('TrainLiveBoards', [])
    return await _cached('train_liveboard', 30, fetch)


async def get_station_liveboard(station_id: str) -> list:
    async def fetch():
        d = await _tdx_get(f'/v3/Rail/TRA/StationLiveBoard/Station/{station_id}')
        return d.get('StationLiveBoards', [])
    return await _cached(f'slb_{station_id}', 30, fetch)


# ── Tourism endpoints ─────────────────────────────────────────────────────────

_EMPTY_DESC = {'無', '—', '-', 'n/a', 'none', ''}

async def get_tourism_spots(city: str) -> list:
    async def fetch():
        raw = await _tdx_get(f'/v2/Tourism/ScenicSpot/{city}', {'$top': '150'})
        spots = raw if isinstance(raw, list) else []
        result = []
        for s in spots:
            pos = s.get('Position') or {}
            slat = pos.get('PositionLat')
            slon = pos.get('PositionLon')
            if slat is None or slon is None:
                continue
            pic = s.get('Picture') or {}
            desc_raw = (s.get('DescriptionDetail') or s.get('Description') or '').strip()
            desc = '' if desc_raw.lower() in _EMPTY_DESC else desc_raw[:150]
            result.append({
                'id': s.get('ScenicSpotID') or s.get('ID', ''),
                'name': s.get('ScenicSpotName') or s.get('Name', ''),
                'description': desc,
                'address': (s.get('Address') or '').strip(),
                'open_time': (s.get('OpenTime') or '').strip()[:80],
                'lat': float(slat),
                'lon': float(slon),
                'picture': pic.get('PictureUrl1', ''),
            })
        return result
    return await _cached(f'tourism_{city}', 21600, fetch)


async def get_daily_timetable(train_no: str) -> dict:
    async def fetch():
        d = await _tdx_get(f'/v3/Rail/TRA/DailyTrainTimetable/Today/TrainNo/{train_no}')
        trains = d.get('TrainTimetables', [])
        return trains[0] if trains else {}
    return await _cached(f'timetable_{train_no}', 3600, fetch)


async def get_station_timetable_today(station_id: str) -> list:
    """
    今日停靠 station_id 的所有班次完整時刻表。
    用 DailyTrainTimetable/Today + OData $filter 取得，
    回傳每班列車的全部停靠站（可跨站查詢終點）。TTL 1h。
    """
    async def fetch():
        d = await _tdx_get('/v3/Rail/TRA/DailyTrainTimetable/Today', {
            '$filter': f"StopTimes/any(s: s/StationID eq '{station_id}')",
            '$top': '500',
        })
        return d.get('TrainTimetables', [])
    return await _cached(f'gts_{station_id}', 3600, fetch)


async def get_station_timetable_date(station_id: str, date_str: str) -> list:
    """指定日期 (YYYYMMDD) 的班表，用於跨午夜查詢。TTL 2h。"""
    async def fetch():
        d = await _tdx_get(f'/v3/Rail/TRA/DailyTrainTimetable/TrainDate/{date_str}', {
            '$filter': f"StopTimes/any(s: s/StationID eq '{station_id}')",
            '$top': '500',
        })
        return d.get('TrainTimetables', [])
    return await _cached(f'gts_{station_id}_{date_str}', 7200, fetch)


# ── Wikipedia 摘要 ─────────────────────────────────────────────────────────────

import urllib.parse

async def get_wiki_summary(name: str) -> str:
    """查 zh.wikipedia.org REST API 取得景點摘要，失敗回傳 ''。"""
    encoded = urllib.parse.quote(name)
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f'https://zh.wikipedia.org/api/rest_v1/page/summary/{encoded}',
                headers={'User-Agent': 'TaiwanRailLive/1.0'},
                follow_redirects=True,
            )
            if r.status_code == 200:
                extract = r.json().get('extract', '')
                if len(extract) > 30:
                    return extract[:200]
    except Exception:
        pass
    return ''


# ── Haversine ─────────────────────────────────────────────────────────────────

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


# ── City lookup from station ──────────────────────────────────────────────────

CITY_MAP = {
    '台北': 'Taipei', '松山': 'Taipei', '南港': 'Taipei', '萬華': 'Taipei',
    '板橋': 'NewTaipei', '汐止': 'NewTaipei', '新店': 'NewTaipei',
    '桃園': 'Taoyuan', '中壢': 'Taoyuan',
    '新竹': 'Hsinchu', '竹北': 'Hsinchu',
    '苗栗': 'MiaoliCounty',
    '台中': 'Taichung', '彰化': 'Changhua',
    '雲林': 'YunlinCounty',
    '嘉義': 'Chiayi',
    '台南': 'Tainan',
    '高雄': 'Kaohsiung', '左營': 'Kaohsiung', '鳳山': 'Kaohsiung',
    '屏東': 'PingtungCounty',
    '花蓮': 'HualienCounty',
    '台東': 'TaitungCounty',
    '宜蘭': 'YilanCounty',
    '基隆': 'Keelung',
}

def station_to_city(station_name: str) -> str:
    for key, city in CITY_MAP.items():
        if key in station_name:
            return city
    return 'Taipei'
