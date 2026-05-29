import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env')
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

import tdx as TDX
import twipcam
import weather as WX
import alerts as ALERTS
from database import (
    init_db, get_social,
    get_stations_age, upsert_stations, get_all_stations,
    get_tourism_age, upsert_tourism, get_tourism_near,
)

app = FastAPI(title='Taiwan Rail Live API')

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['GET'],
    allow_headers=['*'],
)

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'screenshots')

PREFETCH_CITIES = [
    'Taipei', 'NewTaipei', 'Taoyuan', 'Taichung',
    'Tainan', 'Kaohsiung', 'Hsinchu', 'Keelung',
    'HualienCounty', 'TaitungCounty', 'YilanCounty',
    'PingtungCounty', 'Chiayi', 'Changhua',
]


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event('startup')
async def startup():
    init_db()
    asyncio.create_task(_prefetch_static())
    asyncio.create_task(_deferred_scraper())
    asyncio.create_task(ALERTS.alert_loop())


async def _prefetch_static():
    """抓取靜態資料到 DB。車站每週更新，景點每日更新。"""
    # ── Stations ──
    age = get_stations_age()
    if age > 7 * 86400:
        print('[prefetch] 抓取車站資料...')
        try:
            raw = await TDX.get_tra_stations()
            rows = []
            for s in raw:
                pos = s.get('StationPosition') or {}
                lat = pos.get('PositionLat')
                lon = pos.get('PositionLon')
                if lat is None or lon is None:
                    continue
                rows.append({
                    'id': s.get('StationID'),
                    'name': s.get('StationName', {}).get('Zh_tw', ''),
                    'nameEn': s.get('StationName', {}).get('En', ''),
                    'city': s.get('TaiwanTripAdministrativeDivision', ''),
                    'lat': lat,
                    'lon': lon,
                })
            upsert_stations(rows)
            print(f'[prefetch] 車站完成：{len(rows)} 筆')
        except Exception as e:
            print(f'[prefetch] 車站失敗：{e}')
    else:
        print(f'[prefetch] 車站 DB 尚新（{age/3600:.1f}h 前），略過')

    # ── Tourism spots ──
    for city in PREFETCH_CITIES:
        age = get_tourism_age(city)
        if age > 86400:
            for attempt in range(3):
                try:
                    spots = await TDX.get_tourism_spots(city)
                    if spots:
                        upsert_tourism(city, spots)
                        print(f'[prefetch] {city}：{len(spots)} 個景點')
                    await asyncio.sleep(5)
                    break
                except Exception as e:
                    if '429' in str(e):
                        wait = 30 * (attempt + 1)
                        print(f'[prefetch] {city} rate limit，等 {wait}s...')
                        await asyncio.sleep(wait)
                    else:
                        print(f'[prefetch] 景點 {city} 失敗：{e}')
                        break
        else:
            print(f'[prefetch] {city} 景點 DB 尚新，略過')

    # 排程每日重抓景點
    asyncio.create_task(_daily_refresh())


async def _daily_refresh():
    """每 24 小時重抓一次景點資料。"""
    while True:
        await asyncio.sleep(86400)
        print('[refresh] 每日更新景點資料...')
        for city in PREFETCH_CITIES:
            try:
                spots = await TDX.get_tourism_spots(city)
                if spots:
                    upsert_tourism(city, spots)
                await asyncio.sleep(2)
            except Exception as e:
                print(f'[refresh] {city} 失敗：{e}')


async def _deferred_scraper():
    await asyncio.sleep(60)
    try:
        from scraper import scrape_loop
        stations = get_all_stations()
        # 取各城市最近景點名稱作為爬蟲對象
        from database import get_tourism_near
        seen = set()
        names = []
        for s in stations[:50]:
            for spot in get_tourism_near(s['lat'], s['lon'], 2000)[:2]:
                if spot['name'] not in seen:
                    seen.add(spot['name'])
                    names.append(spot['name'])
        if not names:
            names = ['台北車站', '九份', '淡水老街', '西門町', '士林夜市']
        asyncio.create_task(scrape_loop(names, interval_hours=6))
        print(f'[scraper] 啟動，共 {len(names)} 個景點')
    except Exception as e:
        print(f'[scraper] 啟動失敗: {e}')


# ── Train LiveBoard ───────────────────────────────────────────────────────────

@app.get('/api/trains')
async def api_trains():
    boards = await TDX.get_train_liveboard()
    station_map = {s['id']: s for s in get_all_stations()}
    result = []
    for b in boards:
        sid = b.get('StationID', '')
        st = station_map.get(sid)
        if not st:
            continue
        result.append({
            'trainNo': b.get('TrainNo'),
            'trainType': b.get('TrainTypeName', {}).get('Zh_tw', ''),
            'lat': st['lat'],
            'lon': st['lon'],
            'stationName': b.get('StationName', {}).get('Zh_tw', ''),
            'delay': b.get('DelayTime', 0),
        })
    return result


@app.get('/api/featured')
async def api_featured():
    """列車附近景點，供前端滑入卡使用。"""
    boards = await TDX.get_train_liveboard()
    station_map = {s['id']: s for s in get_all_stations()}
    featured = []
    seen_spot_ids = set()
    for b in boards:
        sid = b.get('StationID', '')
        st = station_map.get(sid)
        if not st:
            continue
        spots = get_tourism_near(st['lat'], st['lon'], 3000)
        for spot in spots:
            if spot['id'] not in seen_spot_ids and spot.get('picture'):
                seen_spot_ids.add(spot['id'])
                featured.append({
                    'trainNo': b.get('TrainNo'),
                    'trainType': b.get('TrainTypeName', {}).get('Zh_tw', ''),
                    'stationName': b.get('StationName', {}).get('Zh_tw', ''),
                    'delay': b.get('DelayTime', 0),
                    'attraction': spot,
                })
                break  # one spot per train
        if len(featured) >= 10:
            break
    return featured


# ── Stations（DB 優先）────────────────────────────────────────────────────────

@app.get('/api/stations')
async def api_stations():
    rows = get_all_stations()
    if rows:
        return rows
    # DB 還沒資料，直接打 TDX
    raw = await TDX.get_tra_stations()
    result = []
    for s in raw:
        pos = s.get('StationPosition') or {}
        lat = pos.get('PositionLat')
        lon = pos.get('PositionLon')
        if lat is None or lon is None:
            continue
        result.append({
            'id': s.get('StationID'),
            'name': s.get('StationName', {}).get('Zh_tw', ''),
            'nameEn': s.get('StationName', {}).get('En', ''),
            'city': s.get('TaiwanTripAdministrativeDivision', ''),
            'lat': lat,
            'lon': lon,
        })
    return result


# ── Station LiveBoard ─────────────────────────────────────────────────────────

@app.get('/api/station/{station_id}/liveboard')
async def api_station_liveboard(station_id: str):
    boards = await TDX.get_station_liveboard(station_id)
    result = []
    for b in boards:
        result.append({
            'trainNo': b.get('TrainNo'),
            'trainType': b.get('TrainTypeName', {}).get('Zh_tw', ''),
            'direction': b.get('Direction'),
            'dest': b.get('EndingStationName', {}).get('Zh_tw', ''),
            'scheduledArrival': b.get('ScheduleArrivalTime'),
            'scheduledDepart': b.get('ScheduleDepartureTime'),
            'platform': b.get('Platform'),
            'delay': b.get('DelayTime', 0),
            'tripLine': b.get('TripLine'),
        })
    return result


# ── Schedule（班車查詢）──────────────────────────────────────────────────────

@app.get('/api/schedule')
async def api_schedule(
    station_id: str = Query(...),
    direction: Optional[int] = Query(None),
    trip_line: Optional[int] = Query(None),
):
    boards = await TDX.get_station_liveboard(station_id)
    now = datetime.now()
    result = []
    for b in boards:
        if direction is not None and b.get('Direction') != direction:
            continue
        if trip_line and b.get('TripLine', 0) not in (0, trip_line):
            continue
        depart_str = b.get('ScheduleDepartureTime') or b.get('ScheduleArrivalTime')
        if not depart_str:
            continue
        try:
            h, m = map(int, depart_str.split(':')[:2])
            delta = (h * 60 + m) - (now.hour * 60 + now.minute)
            if delta < -720:
                delta += 1440  # midnight wrap
        except Exception:
            continue
        if not (-5 <= delta <= 60):
            continue
        result.append({
            'trainNo': b.get('TrainNo'),
            'trainType': b.get('TrainTypeName', {}).get('Zh_tw', ''),
            'direction': b.get('Direction'),
            'dest': b.get('EndingStationName', {}).get('Zh_tw', ''),
            'depart': depart_str[:5],
            'platform': b.get('Platform', ''),
            'delay': b.get('DelayTime', 0),
            'tripLine': b.get('TripLine', 0),
            'minutesUntil': delta,
        })
    result.sort(key=lambda x: x['minutesUntil'])
    return result


@app.get('/api/train/{train_no}/route')
async def api_train_route(train_no: str, from_station: str = Query('')):
    timetable = await TDX.get_daily_timetable(train_no)
    stops = timetable.get('StopTimes', [])
    start_idx = 0
    for i, s in enumerate(stops):
        if s.get('StationID') == from_station:
            start_idx = i
            break
    result = []
    for s in stops[start_idx:]:
        result.append({
            'stationId': s.get('StationID'),
            'stationName': s.get('StationName', {}).get('Zh_tw', ''),
            'arrival': s.get('ArrivalTime'),
            'departure': s.get('DepartureTime'),
        })
    return result


# ── Station train types（班車查詢用）─────────────────────────────────────────

@app.get('/api/station/{station_id}/types')
async def api_station_types(station_id: str):
    boards = await TDX.get_station_liveboard(station_id)
    now = datetime.now()
    types_seen: dict = {}
    for b in boards:
        depart_str = b.get('ScheduleDepartureTime') or b.get('ScheduleArrivalTime')
        if not depart_str:
            continue
        h, m = map(int, depart_str.split(':')[:2])
        delta = (h * 60 + m) - (now.hour * 60 + now.minute)
        if delta < -720:
            delta += 1440
        if not (-5 <= delta <= 120):
            continue
        type_name = b.get('TrainTypeName', {}).get('Zh_tw', '')
        trip_line = b.get('TripLine', 0)
        if not type_name:
            continue
        if type_name not in types_seen:
            types_seen[type_name] = {'name': type_name, 'hasTripLine': False}
        if trip_line in (1, 2):
            types_seen[type_name]['hasTripLine'] = True
    return list(types_seen.values())


# ── Trip search A → B ────────────────────────────────────────────────────────

@app.get('/api/trips')
async def api_trips(
    from_station: str = Query(...),
    to_station: str = Query(...),
    train_type: str = Query(''),
    trip_line: Optional[int] = Query(None),
):
    boards = await TDX.get_station_liveboard(from_station)
    now = datetime.now()
    candidates = []
    for b in boards:
        type_name = b.get('TrainTypeName', {}).get('Zh_tw', '')
        if train_type and train_type != type_name:
            continue
        if trip_line and b.get('TripLine', 0) not in (0, trip_line):
            continue
        depart_str = b.get('ScheduleDepartureTime') or b.get('ScheduleArrivalTime')
        if not depart_str:
            continue
        h, m = map(int, depart_str.split(':')[:2])
        delta = (h * 60 + m) - (now.hour * 60 + now.minute)
        if delta < -720:
            delta += 1440
        if not (-5 <= delta <= 120):
            continue
        candidates.append(b)

    sem = asyncio.Semaphore(3)

    async def _fetch(train_no):
        async with sem:
            return await TDX.get_daily_timetable(train_no)

    timetables = await asyncio.gather(
        *[_fetch(b.get('TrainNo')) for b in candidates],
        return_exceptions=True,
    )

    result = []
    for b, timetable in zip(candidates, timetables):
        if isinstance(timetable, Exception):
            continue
        stops = timetable.get('StopTimes', [])
        from_idx = to_idx = None
        for i, s in enumerate(stops):
            if s.get('StationID') == from_station and from_idx is None:
                from_idx = i
            if s.get('StationID') == to_station and from_idx is not None:
                to_idx = i
                break
        if from_idx is None or to_idx is None:
            continue

        dep = (stops[from_idx].get('DepartureTime') or stops[from_idx].get('ArrivalTime') or '')[:5]
        arr = (stops[to_idx].get('ArrivalTime') or stops[to_idx].get('DepartureTime') or '')[:5]
        try:
            d_h, d_m = map(int, dep.split(':'))
            a_h, a_m = map(int, arr.split(':'))
            journey_min = (a_h * 60 + a_m) - (d_h * 60 + d_m)
            if journey_min < 0:
                journey_min += 1440
        except Exception:
            journey_min = 0

        route_stops = []
        for s in stops[from_idx:to_idx + 1]:
            route_stops.append({
                'stationId': s.get('StationID'),
                'stationName': s.get('StationName', {}).get('Zh_tw', ''),
                'arrival': (s.get('ArrivalTime') or '')[:5],
                'departure': (s.get('DepartureTime') or '')[:5],
            })

        result.append({
            'trainNo': b.get('TrainNo'),
            'trainType': b.get('TrainTypeName', {}).get('Zh_tw', ''),
            'direction': b.get('Direction'),
            'depart': dep,
            'arrive': arr,
            'journeyMin': journey_min,
            'platform': b.get('Platform', ''),
            'delay': b.get('DelayTime', 0),
            'tripLine': b.get('TripLine', 0),
            'stops': route_stops,
        })

    result.sort(key=lambda x: x['depart'])
    return result


# ── Attractions（DB 優先）─────────────────────────────────────────────────────

@app.get('/api/attractions')
async def api_attractions(
    lat: float = Query(...),
    lon: float = Query(...),
    radius: float = Query(800),
    city: str = Query('Taipei'),
):
    # 先查本地 DB（跨城市）
    rows = get_tourism_near(lat, lon, radius)
    if rows:
        return rows

    # DB 無資料則 fallback 到 TDX 即時查詢
    spots = await TDX.get_tourism_spots(city)
    result = []
    for s in spots:
        dist = TDX.haversine(lat, lon, s['lat'], s['lon'])
        if dist <= radius:
            result.append({**s, 'distance_m': round(dist)})
    result.sort(key=lambda x: x['distance_m'])
    return result


# ── Cameras (Twipcam) ─────────────────────────────────────────────────────────

@app.get('/api/cameras')
async def api_cameras(lat: float = Query(...), lon: float = Query(...)):
    return await twipcam.get_nearby_cameras(lat, lon, radius_m=1500, limit=6)


# ── Weather (CWA) ─────────────────────────────────────────────────────────────

@app.get('/api/weather')
async def api_weather(lat: float = Query(...), lon: float = Query(...)):
    return await WX.get_weather_near(lat, lon)


# ── Alerts ────────────────────────────────────────────────────────────────────

@app.get('/api/alerts')
async def api_alerts():
    return ALERTS.get_alerts()


# ── Social Content ────────────────────────────────────────────────────────────

@app.get('/api/social/{attraction}')
async def api_social(attraction: str):
    return get_social(attraction)


# ── Screenshot static files ───────────────────────────────────────────────────

@app.get('/screenshots/{filename}')
async def serve_screenshot(filename: str):
    fpath = os.path.join(SCREENSHOT_DIR, filename)
    if not os.path.exists(fpath):
        raise HTTPException(404, 'screenshot not found')
    return FileResponse(fpath, media_type='image/jpeg')


# ── Portal ────────────────────────────────────────────────────────────────────

PORTAL_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>即時監控中心</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%}
body{
  background:#05080f;font-family:'Courier New',Consolas,monospace;
  overflow:hidden;display:flex;flex-direction:column;
}
#nav{
  flex:0 0 38px;background:#080d1a;
  border-bottom:1px solid #142840;
  display:flex;align-items:center;padding:0 10px;gap:4px;
}
.tab{
  padding:0 14px;height:26px;border:1px solid #142840;border-radius:3px;
  background:transparent;color:#4a6080;
  font-family:'Courier New',Consolas,monospace;font-size:11px;letter-spacing:.5px;
  cursor:pointer;transition:all .15s;white-space:nowrap;
}
.tab:hover{color:#c0d4f0;border-color:#1e4070;background:rgba(0,212,255,.03)}
.tab.active{color:#00d4ff;border-color:#00d4ff;background:rgba(0,212,255,.07)}
#logo{
  margin-left:auto;font-size:9px;color:#1e3050;
  letter-spacing:2px;text-transform:uppercase;
}
/* ── Alert bar ── */
#alert-bar{
  flex:0 0 auto;background:#060b16;
  border-bottom:1px solid #142840;
  overflow:hidden;
}
#alert-bar:empty{border-bottom:none}
.alert-row{
  display:flex;align-items:center;gap:10px;
  padding:4px 12px;font-size:11px;letter-spacing:.3px;
  border-bottom:1px solid rgba(255,255,255,.04);
}
.alert-row:last-child{border-bottom:none}
.alert-row.red   {border-left:3px solid #ff2040;color:#ff6070}
.alert-row.orange{border-left:3px solid #ff7800;color:#ffaa40}
.alert-row.yellow{border-left:3px solid #ccaa00;color:#ffd700}
.alert-tag{font-weight:bold;white-space:nowrap;min-width:72px}
.alert-msg{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.alert-time{color:#304060;font-size:10px;white-space:nowrap}
/* ── iframes ── */
#frames{flex:1;position:relative;overflow:hidden}
iframe{
  position:absolute;inset:0;width:100%;height:100%;
  border:none;opacity:0;pointer-events:none;transition:opacity .2s;
}
iframe.active{opacity:1;pointer-events:auto}
</style>
</head>
<body>
<nav id="nav">
  <button class="tab active" onclick="show('hydro',this)">🌊 北台灣水文</button>
  <button class="tab" onclick="show('taipei',this)">🏙️ 台北看板</button>
  <button class="tab" onclick="show('rail',this)">🚂 台鐵即時</button>
  <span id="logo">即時監控中心 · LIVE DASHBOARD</span>
</nav>
<div id="alert-bar"></div>
<div id="frames">
  <iframe id="hydro" src="/hydro/" class="active"></iframe>
  <iframe id="taipei" src="/taipei/"></iframe>
  <iframe id="rail" src="/rail/"></iframe>
</div>
<script>
function show(id,btn){
  document.querySelectorAll('iframe').forEach(f=>f.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}

const EMOJI = {red:'🔴',orange:'🟠',yellow:'🟡'};

async function refreshAlerts(){
  try{
    const alerts = await fetch('/api/alerts').then(r=>r.json());
    const bar = document.getElementById('alert-bar');
    bar.innerHTML = alerts.map(a=>
      `<div class="alert-row ${a.level}" title="${a.detail||''}">
        <span>${EMOJI[a.level]||'⚪'}</span>
        <span class="alert-tag">${a.type}</span>
        <span class="alert-msg">${a.summary}</span>
        <span class="alert-time">更新 ${a.updated_at}</span>
      </div>`
    ).join('');
  }catch(e){console.warn('[alerts]',e)}
}

refreshAlerts();
setInterval(refreshAlerts, 60000);
</script>
</body>
</html>"""

@app.get('/', response_class=HTMLResponse)
async def portal():
    return HTMLResponse(PORTAL_HTML)


# ── Taipei Dashboard proxy ────────────────────────────────────────────────────

TAIPEI_ORIGIN = 'http://localhost:5555'

@app.get('/taipei/')
@app.get('/taipei')
async def taipei_home():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f'{TAIPEI_ORIGIN}/', timeout=10)
        html = resp.text
        # Rewrite relative /api/ calls so they route through /taipei/api/
        html = html.replace("fetch('/api/", "fetch('/taipei/api/")
        html = html.replace('fetch("/api/', 'fetch("/taipei/api/')
        html = html.replace('fetch(`/api/', 'fetch(`/taipei/api/')
        html = html.replace("? '/api/", "? '/taipei/api/")
        html = html.replace(': \'/api/', ': \'/taipei/api/')
        return HTMLResponse(html)
    except Exception:
        return HTMLResponse(
            '<body style="background:#05080f;color:#ff2255;font-family:monospace;padding:40px">'
            '⚠ 台北看板未啟動，請先執行 dashboard.py (port 5555)</body>',
            status_code=503,
        )

@app.get('/taipei/api/{path:path}')
async def taipei_api(path: str, request: Request):
    params = dict(request.query_params)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f'{TAIPEI_ORIGIN}/api/{path}', params=params, timeout=15)
        return Response(content=resp.content,
                        media_type=resp.headers.get('content-type', 'application/json'))
    except Exception as e:
        return Response(content=b'{"status":"error","message":"taipei proxy error"}',
                        media_type='application/json', status_code=503)


# ── Frontend（必須最後 mount）────────────────────────────────────────────────

FRONTEND_DIST = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'dist')
HYDRO_DIST    = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'Projects', 'hydro-monitor', 'out')

_MIME = {'.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html'}

class ServePrecompressedMiddleware(BaseHTTPMiddleware):
    """Serve pre-compressed .gz files for /rail/assets/* when client accepts gzip."""
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path.startswith('/rail/assets/') and 'gzip' in request.headers.get('accept-encoding', ''):
            rel = path[len('/rail/'):]   # strip /rail/ prefix → assets/foo.js
            gz = os.path.join(FRONTEND_DIST, rel + '.gz')
            if os.path.exists(gz):
                ext = os.path.splitext(path)[1]
                with open(gz, 'rb') as f:
                    data = f.read()
                return Response(
                    content=data,
                    media_type=_MIME.get(ext, 'application/octet-stream'),
                    headers={
                        'Content-Encoding': 'gzip',
                        'Cache-Control': 'public, max-age=31536000, immutable',
                        'Vary': 'Accept-Encoding',
                    },
                )
        response = await call_next(request)
        if path.startswith('/rail/assets/'):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        elif path in ('/rail/', '/rail/index.html'):
            response.headers['Cache-Control'] = 'no-cache'
        return response

if os.path.isdir(HYDRO_DIST):
    app.mount('/hydro', StaticFiles(directory=HYDRO_DIST, html=True), name='hydro')

if os.path.isdir(FRONTEND_DIST):
    app.add_middleware(ServePrecompressedMiddleware)
    app.mount('/rail', StaticFiles(directory=FRONTEND_DIST, html=True), name='frontend')
