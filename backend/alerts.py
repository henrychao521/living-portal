"""
即時警報彙整模組
輪詢 CWA 颱風/特報/地震 + NCDR 淹水 RSS，統一提供 /api/alerts
"""
import asyncio
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
import httpx
import hashlib

CWA_KEY  = os.environ.get('CWA_API_KEY', '')  # 金鑰一律走 .env,絕不進 repo(2026-08 已因外洩換發)
CWA_BASE = 'https://opendata.cwa.gov.tw/api/v1/rest/datastore'

TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

LEVEL_ORDER = {'red': 0, 'orange': 1, 'yellow': 2}

@dataclass
class Alert:
    id: str
    level: str   # red / orange / yellow
    type: str
    summary: str
    detail: str
    updated_at: str
    notified: bool = False

_alerts: dict[str, Alert] = {}
_lock = asyncio.Lock()


def get_alerts() -> list[dict]:
    result = sorted(_alerts.values(), key=lambda a: LEVEL_ORDER.get(a.level, 9))
    return [
        {'id': a.id, 'level': a.level, 'type': a.type,
         'summary': a.summary, 'detail': a.detail, 'updated_at': a.updated_at}
        for a in result
    ]


async def _telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        async with httpx.AsyncClient() as c:
            await c.post(
                f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
                json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
                timeout=10,
            )
    except Exception as e:
        print(f'[alerts] Telegram 失敗：{e}')


def _now() -> str:
    return datetime.now().strftime('%H:%M')


async def _upsert(alert: Alert):
    async with _lock:
        prev = _alerts.get(alert.id)
        is_new = prev is None or prev.summary != alert.summary
        if prev:
            alert.notified = prev.notified
        _alerts[alert.id] = alert
        if is_new and not alert.notified:
            _alerts[alert.id].notified = True
            emoji = {'red': '🔴', 'orange': '🟠', 'yellow': '🟡'}.get(alert.level, '⚪')
            await _telegram(
                f'{emoji} <b>{alert.type}</b>\n{alert.summary}\n'
                f'🔗 https://henrylivingtech.com'
            )


async def _clear_stale(keep: set, prefix: str):
    async with _lock:
        for k in [k for k in list(_alerts) if k.startswith(prefix) and k not in keep]:
            del _alerts[k]


# ── CWA 颱風警報 W-C0034-001 ─────────────────────────────────────────────────

async def _fetch_typhoon():
    try:
        async with httpx.AsyncClient(verify=False) as c:
            r = await c.get(f'{CWA_BASE}/W-C0034-001',
                            params={'Authorization': CWA_KEY, 'format': 'JSON'}, timeout=15)
        records = r.json().get('records', {}).get('record', [])
        keep: set = set()
        for rec in records:
            aid = f'typhoon-{rec.get("issueTime","")}'
            keep.add(aid)
            hazards = rec.get('hazardConditions', {}).get('hazards', {}).get('hazard', [{}])
            phenomena = hazards[0].get('info', {}).get('phenomena', '') if hazards else ''
            contents = rec.get('contents', {}).get('content', [])
            detail = contents[0].get('contentText', '')[:300] if contents else ''
            summary = phenomena or rec.get('datasetDescription', '颱風警報')
            await _upsert(Alert(id=aid, level='red', type='颱風警報',
                                summary=summary, detail=detail, updated_at=_now()))
        await _clear_stale(keep, 'typhoon-')
    except Exception as e:
        print(f'[alerts] 颱風警報失敗：{e}')


# ── CWA 特報 W-C0033-001（大雨/強風/陸上強風等）─────────────────────────────

async def _fetch_special():
    try:
        async with httpx.AsyncClient(verify=False) as c:
            r = await c.get(f'{CWA_BASE}/W-C0033-001',
                            params={'Authorization': CWA_KEY, 'format': 'JSON'}, timeout=15)
        records = r.json().get('records', {}).get('record', [])
        keep: set = set()
        for rec in records:
            hazards = rec.get('hazardConditions', {}).get('hazards', {}).get('hazard', [])
            for hz in hazards:
                areas = hz.get('affectedAreas', {}).get('location', [])
                area_names = '、'.join(a.get('locationName', '') for a in areas[:6])
                phenomena = hz.get('info', {}).get('phenomena', rec.get('datasetDescription', '特報'))
                raw_id = phenomena + area_names + rec.get('issueTime', '')
                aid = f'special-{hashlib.md5(raw_id.encode()).hexdigest()[:6]}'
                keep.add(aid)
                level = 'red' if any(k in phenomena for k in ('豪雨', '大豪雨', '颱風')) else 'orange'
                await _upsert(Alert(id=aid, level=level, type=phenomena,
                                    summary=area_names or '全台', detail='', updated_at=_now()))
        await _clear_stale(keep, 'special-')
    except Exception as e:
        print(f'[alerts] 特報失敗：{e}')


# ── NCDR 淹水警戒 RSS ─────────────────────────────────────────────────────────

async def _fetch_flood():
    try:
        async with httpx.AsyncClient(verify=False) as c:
            r = await c.get('https://www.ncdr.nat.gov.tw/cgi-bin/rss.cgi', timeout=15)
        root = ET.fromstring(r.text)
        keep: set = set()
        for item in root.findall('.//item'):
            title = item.findtext('title') or ''
            if not any(k in title for k in ('淹水', '警戒', '水位')):
                continue
            aid = f'flood-{hashlib.md5(title.encode()).hexdigest()[:6]}'
            keep.add(aid)
            await _upsert(Alert(id=aid, level='orange', type='淹水警戒',
                                summary=title, detail='', updated_at=_now()))
        await _clear_stale(keep, 'flood-')
    except Exception as e:
        print(f'[alerts] NCDR RSS 失敗：{e}')


# ── CWA 地震速報 E-A0015-001 ─────────────────────────────────────────────────

async def _fetch_quake():
    try:
        async with httpx.AsyncClient(verify=False) as c:
            r = await c.get(f'{CWA_BASE}/E-A0015-001',
                            params={'Authorization': CWA_KEY, 'format': 'JSON', 'limit': '10'},
                            timeout=15)
        quakes = r.json().get('records', {}).get('Earthquake', [])
        # 取近 10 筆中規模最大且仍在時窗內者——limit=1 時最新一筆小震會清掉先前大震警報
        q = None
        for cand in quakes:
            m = float(cand.get('EarthquakeInfo', {}).get('EarthquakeMagnitude', {}).get('MagnitudeValue', 0))
            if m >= 4.0 and (q is None or m > float(q.get('EarthquakeInfo', {}).get('EarthquakeMagnitude', {}).get('MagnitudeValue', 0))):
                q = cand
        if q is None:
            await _clear_stale(set(), 'quake-')
            return
        info = q.get('EarthquakeInfo', {})
        mag = float(info.get('EarthquakeMagnitude', {}).get('MagnitudeValue', 0))
        # 超過 30 分鐘不再顯示
        origin_str = info.get('OriginTime', '')
        try:
            origin_dt = datetime.fromisoformat(origin_str)
            if (datetime.now() - origin_dt).total_seconds() > 1800:
                await _clear_stale(set(), 'quake-')
                return
        except Exception:
            pass
        aid = f'quake-{q.get("EarthquakeNo", 0)}'
        loc = info.get('Epicenter', {}).get('Location', '未知')
        depth = info.get('FocalDepth', '?')
        level = 'red' if mag >= 6.0 else 'orange' if mag >= 5.0 else 'yellow'
        await _upsert(Alert(id=aid, level=level, type='地震速報',
                            summary=f'規模 {mag}，{loc}',
                            detail=f'深度 {depth} 公里，{origin_str[:16]}',
                            updated_at=_now()))
    except Exception as e:
        print(f'[alerts] 地震速報失敗：{e}')


# ── 主排程迴圈 ────────────────────────────────────────────────────────────────

async def alert_loop():
    print('[alerts] 啟動警報輪詢...')
    tick = 0
    while True:
        await _fetch_quake()            # 每 60 秒
        if tick % 10 == 0:
            await _fetch_flood()        # 每 10 分鐘
            await _fetch_special()      # 每 10 分鐘
        if tick % 15 == 0:
            await _fetch_typhoon()      # 每 15 分鐘
        tick += 1
        await asyncio.sleep(60)
