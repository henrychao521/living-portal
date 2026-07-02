import math
import sqlite3
import time
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'rail.db')


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS social_content (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            attraction   TEXT NOT NULL,
            source_url   TEXT NOT NULL UNIQUE,
            title        TEXT,
            screenshot   TEXT,
            scraped_at   REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attraction ON social_content(attraction);

        CREATE TABLE IF NOT EXISTS stations (
            id        TEXT PRIMARY KEY,
            name      TEXT NOT NULL,
            name_en   TEXT,
            city      TEXT,
            lat       REAL NOT NULL,
            lon       REAL NOT NULL,
            fetched_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tourism_spots (
            id              TEXT PRIMARY KEY,
            city_key        TEXT NOT NULL,
            name            TEXT NOT NULL,
            description     TEXT,
            address         TEXT DEFAULT '',
            open_time       TEXT DEFAULT '',
            wiki_description TEXT DEFAULT '',
            lat             REAL NOT NULL,
            lon             REAL NOT NULL,
            picture         TEXT,
            fetched_at      REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tourism_city ON tourism_spots(city_key);
        CREATE INDEX IF NOT EXISTS idx_tourism_latlon ON tourism_spots(lat, lon);
    ''')
    # Migrate existing tables: add columns if missing
    # address/open_time added in v0.8; wiki_description added in v0.9
    # If any new column is added, clear old data so prefetch re-fetches with correct schema
    needs_refetch = False
    for col, col_def in [
        ('address',          "TEXT DEFAULT ''"),
        ('open_time',        "TEXT DEFAULT ''"),
        ('wiki_description', "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(f'ALTER TABLE tourism_spots ADD COLUMN {col} {col_def}')
            needs_refetch = True
        except sqlite3.OperationalError:
            pass  # column already exists
    if needs_refetch:
        conn.execute('DELETE FROM tourism_spots')
    conn.commit()
    conn.close()


# ── Social content ────────────────────────────────────────────────────────────

def get_social(attraction: str, limit: int = 9) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT * FROM social_content WHERE attraction=? ORDER BY scraped_at DESC LIMIT ?',
        (attraction, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_social(attraction, source_url, title, screenshot, scraped_at):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        INSERT INTO social_content(attraction, source_url, title, screenshot, scraped_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(source_url) DO UPDATE SET
            title=excluded.title, screenshot=excluded.screenshot, scraped_at=excluded.scraped_at
    ''', (attraction, source_url, title, screenshot, scraped_at))
    conn.commit()
    conn.close()


# ── Stations ──────────────────────────────────────────────────────────────────

def get_stations_age() -> float:
    """Seconds since stations were last fetched. Returns inf if empty."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute('SELECT MIN(fetched_at) FROM stations').fetchone()
    conn.close()
    return time.time() - row[0] if (row and row[0]) else float('inf')


def upsert_stations(rows: list):
    now = time.time()
    conn = sqlite3.connect(DB_PATH)
    conn.executemany('''
        INSERT INTO stations(id, name, name_en, city, lat, lon, fetched_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, name_en=excluded.name_en, city=excluded.city,
            lat=excluded.lat, lon=excluded.lon, fetched_at=excluded.fetched_at
    ''', [(r['id'], r['name'], r['nameEn'], r['city'], r['lat'], r['lon'], now)
          for r in rows])
    conn.commit()
    conn.close()


def get_all_stations() -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM stations ORDER BY name').fetchall()
    conn.close()
    return [{'id': r['id'], 'name': r['name'], 'nameEn': r['name_en'],
             'city': r['city'], 'lat': r['lat'], 'lon': r['lon']} for r in rows]


# ── Tourism spots ─────────────────────────────────────────────────────────────

def get_tourism_age(city_key: str) -> float:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute('SELECT MIN(fetched_at) FROM tourism_spots WHERE city_key=?',
                       (city_key,)).fetchone()
    conn.close()
    return time.time() - row[0] if (row and row[0]) else float('inf')


def upsert_tourism(city_key: str, rows: list):
    """Insert/update tourism spots, preserving wiki_description from previous enrichment."""
    now = time.time()
    conn = sqlite3.connect(DB_PATH)
    # UPSERT: update all TDX fields but leave wiki_description untouched
    conn.executemany('''
        INSERT INTO tourism_spots(id, city_key, name, description, address, open_time, lat, lon, picture, fetched_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            city_key=excluded.city_key, name=excluded.name,
            description=excluded.description, address=excluded.address,
            open_time=excluded.open_time, lat=excluded.lat, lon=excluded.lon,
            picture=excluded.picture, fetched_at=excluded.fetched_at
    ''', [(r['id'], city_key, r['name'], r['description'],
           r.get('address', ''), r.get('open_time', ''),
           r['lat'], r['lon'], r['picture'], now) for r in rows])
    # Remove spots no longer in API response for this city
    if rows:
        placeholders = ','.join('?' * len(rows))
        new_ids = [r['id'] for r in rows]
        conn.execute(
            f'DELETE FROM tourism_spots WHERE city_key=? AND id NOT IN ({placeholders})',
            [city_key] + new_ids,
        )
    else:
        conn.execute('DELETE FROM tourism_spots WHERE city_key=?', (city_key,))
    conn.commit()
    conn.close()


def get_tourism_spots_missing_desc(limit: int = 300) -> list:
    """Return spots with no TDX description and no wiki_description yet."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('''
        SELECT id, name FROM tourism_spots
        WHERE (description IS NULL OR description = '')
          AND (wiki_description IS NULL OR wiki_description = '')
        LIMIT ?
    ''', (limit,)).fetchall()
    conn.close()
    return [{'id': r['id'], 'name': r['name']} for r in rows]


def bulk_update_wiki_descriptions(updates: list):
    """Updates: list of (spot_id, wiki_description)."""
    conn = sqlite3.connect(DB_PATH)
    conn.executemany(
        'UPDATE tourism_spots SET wiki_description=? WHERE id=?',
        [(desc, spot_id) for spot_id, desc in updates],
    )
    conn.commit()
    conn.close()


def get_tourism_near(lat: float, lon: float, radius_m: float) -> list:
    """Haversine filter across all cached tourism spots."""
    from tdx import haversine
    dlat = radius_m / 111000
    dlon = radius_m / max(111000 * math.cos(math.radians(lat)), 1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('''
        SELECT * FROM tourism_spots
        WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
    ''', (lat - dlat, lat + dlat, lon - dlon, lon + dlon)).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = haversine(lat, lon, r['lat'], r['lon'])
        if d <= radius_m:
            result.append({
                'id': r['id'], 'name': r['name'],
                # wiki_description as fallback when TDX description is empty
                'description': r['description'] or r['wiki_description'] or '',
                'address': r['address'] or '',
                'open_time': r['open_time'] or '',
                'lat': r['lat'], 'lon': r['lon'],
                'picture': r['picture'] or '',
                'distance_m': round(d),
            })
    result.sort(key=lambda x: x['distance_m'])
    return result
