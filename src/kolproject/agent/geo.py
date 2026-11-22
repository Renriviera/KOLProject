"""Geography layer of the agent tool: where each CAR-T center and clinician sits, and the outlines drawn under them.

`uv run python -m kolproject.agent.geo` builds, under `agent/pack/geo/` (not in Git: it holds address-level
coordinates), `states.geojson` (the 50 states, DC and Puerto Rico of the Census 1:20,000,000 cartographic boundary
file), `zcta.sqlite` (every 2020 ZIP Code Tabulation Area of the 1:500,000 file, simplified, with an R*Tree over the
bounding boxes), `center_points.csv`, `hcp_points.csv` and `geo_manifest.json`.

A center's address is its row in the CMS hospital enrollment file. A clinician's is the practice address in the CMS
Doctors and Clinicians file whose ZIP equals the clinician's ZIP in `hcp_divisions.csv`, else whose city and state
match, else the first listed. Each distinct address goes once to the Census Geocoder batch service as street line,
city, state and ZIP under a running row number; no name field, NPI or CCN leaves this machine. A geocoded point is
kept only inside the bounding box of the expected ZCTA (the ZIP of the published table) widened by MARGIN degrees,
or inside the expected state's box when that ZIP has no ZCTA. Otherwise the point is the ZCTA centroid, then the mean
centroid of the ZCTAs that the tables and the two address files list for the same city and state, then blank.
`geo_precision` names the rule that applied and `geocoder_match` what the service answered (exact, non_exact, tie,
no_match, or no_address when nothing was sent). Every response is frozen under `Data/raw/2026-11-20/agent_geo/`
beside its request and a retrieval record, and an address already answered is never sent again.

`zcta_features`, `states_geojson` and `load_points` serve the web server from those files and never use the network.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import time
import zipfile
from collections import Counter, defaultdict
from contextlib import closing

import numpy as np
import requests
import shapefile
import shapely
from shapely.geometry import mapping, shape

from ..common import ROOT, dump, load, now, read_csv, write_csv
from ..extensions.base import RAW, fetch
from .paths import GEO, PROCESSED, RAW_BULK

MODULE = 'agent_geo'
RAW_GEO = RAW / MODULE
UNIVERSE = PROCESSED / '2026-10-24' / 'hcp_divisions.csv'
CENTERS = PROCESSED / '2026-10-24' / 'cart_centers.csv'
PRACTICES = RAW_BULK / 'DAC_NationalDownloadableFile.csv'
HOSPITALS = RAW_BULK / 'Hospital_Enrollments_2026.09.29.csv'
# US Census Bureau cartographic boundary files (public domain), linked from
# https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html
STATES_URL = 'https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_state_20m.zip'
ZCTA_URL = 'https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip'
# https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html: multipart POST, at most 10,000 rows a file.
GEOCODER = 'https://geocoding.geo.census.gov/geocoder/locations/addressbatch'
BENCHMARK = 'Public_AR_Current'
BATCH, SMALLEST, TRIES, PAUSE, GAP = 5000, 1000, 3, 60, 2
# Simplification runs over the whole tiling, so neighbours keep one shared border: a vertex goes when the triangle it
# makes with its neighbours is under about TOLERANCE squared (0.0005 degrees is roughly 50 metres). The 1:500,000 file
# is already generalized near that level, so this trims about 7% of its vertices and leaves zcta.sqlite near 130 MB;
# 0.0009 gives about 118 MB but turns the smallest downtown ZCTAs into triangles.
TOLERANCE = 0.0005
DIGITS = 5
MARGIN = 0.25
PRECISIONS = ['address', 'zip_centroid', 'city_state_centroid', 'none']
UNANSWERED = ('no_match', None, None)
POINT_FIELDS = ['lat', 'lon', 'geo_precision', 'geocoder_match']


# ---- outlines ----

def read_shapes(path):
    """(attributes, shapely geometry) for every shape of the one shapefile inside a Census zip, read in memory."""
    with zipfile.ZipFile(path) as archive:
        stem = next(name for name in archive.namelist() if name.endswith('.shp'))[:-4]
        parts = {part: io.BytesIO(archive.read(f'{stem}.{part}')) for part in ('shp', 'shx', 'dbf')}
    reader = shapefile.Reader(**parts)
    return [(item.record.as_dict(), shape(item.shape.__geo_interface__)) for item in reader.iterShapeRecords()]


def polygonal(geom):
    """The polygon parts of any geometry as one Polygon or MultiPolygon (empty when there is none)."""
    parts = [g for g in shapely.get_parts(shapely.get_parts(geom)) if g.geom_type == 'Polygon' and not g.is_empty]
    return parts[0] if len(parts) == 1 else shapely.MultiPolygon(parts)


def repair(geom):
    """A valid polygon on the DIGITS grid from one that rounding broke; a shape is never silently dropped."""
    fixed = polygonal(shapely.set_precision(polygonal(shapely.make_valid(geom)), 10 ** -DIGITS))
    if fixed.is_empty or not fixed.is_valid:
        raise ValueError('A polygon could not be kept valid at the output precision')
    return fixed


def snap(geoms):
    """Round every coordinate to DIGITS decimals, wind rings as GeoJSON asks, and repair what the rounding broke."""
    rounded = shapely.transform(np.asarray(list(geoms), dtype=object), lambda xy: np.round(xy, DIGITS))
    rounded = shapely.orient_polygons(shapely.remove_repeated_points(rounded))
    return [g if g.is_valid and not g.is_empty else shapely.orient_polygons(repair(g)) for g in rounded]


def simplify(geoms, tolerance=TOLERANCE):
    """Simplify polygons that tile an area so that shared borders stay shared, then `snap` them."""
    return snap(shapely.coverage_simplify(np.asarray(list(geoms), dtype=object), tolerance))


def geometry_json(geom):
    """A compact GeoJSON geometry string with DIGITS decimals."""
    def trim(value):
        if isinstance(value[0], (tuple, list)):
            return [trim(v) for v in value]
        return [round(value[0], DIGITS), round(value[1], DIGITS)]
    plain = mapping(geom)
    return json.dumps({'type': plain['type'], 'coordinates': trim(plain['coordinates'])}, separators=(',', ':'))


def write_zcta(path, records, facts=None):
    """Write the ZCTA table, its R*Tree and a few build facts; `records` are (zcta5, polygon, centroid_lon, centroid_lat).

    Rows go in ZIP order, which keeps neighbours on the same pages, and pages are large so that almost every outline
    sits inside its row. The R*Tree repeats the code and centroid, so a box query reads no outline it will not return.
    """
    temporary = path.with_name(path.name + '.tmp')
    temporary.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(temporary)) as db:
        db.executescript('''PRAGMA page_size = 65536;
            CREATE TABLE zcta(zcta5 TEXT PRIMARY KEY, min_lon REAL, min_lat REAL, max_lon REAL, max_lat REAL,
                              centroid_lon REAL, centroid_lat REAL, geometry TEXT);
            CREATE VIRTUAL TABLE zcta_box USING rtree(id, min_lon, max_lon, min_lat, max_lat,
                                                      +zcta5 TEXT, +centroid_lon REAL, +centroid_lat REAL);
            CREATE TABLE facts(key TEXT PRIMARY KEY, value TEXT);''')
        for code, geom, lon, lat in sorted(records, key=lambda r: r[0]):
            west, south, east, north = geom.bounds
            number = db.execute('INSERT INTO zcta VALUES (?,?,?,?,?,?,?,?)',
                                (code, west, south, east, north, lon, lat, geometry_json(geom))).lastrowid
            db.execute('INSERT INTO zcta_box VALUES (?,?,?,?,?,?,?,?)', (number, west, east, south, north, code, lon, lat))
        db.executemany('INSERT INTO facts VALUES (?,?)', [(k, str(v)) for k, v in (facts or {}).items()])
        db.commit()
    temporary.replace(path)


def connect():
    """A read-only connection to the ZCTA table; one per call, so any server thread may ask."""
    return sqlite3.connect((GEO / 'zcta.sqlite').as_uri() + '?mode=ro', uri=True)


def zcta_features(min_lon, min_lat, max_lon, max_lat, limit=1500):
    """ZCTAs whose bounding box meets the box, as a GeoJSON FeatureCollection with properties {zcta5}.

    When more than `limit` match, the `limit` whose centroids are nearest the box centre are returned and the
    top-level key "truncated" is true (it is false otherwise).
    """
    with closing(connect()) as db:
        found = db.execute('SELECT id, zcta5, centroid_lon, centroid_lat FROM zcta_box WHERE min_lon <= ? '
                           'AND max_lon >= ? AND min_lat <= ? AND max_lat >= ?', (max_lon, min_lon, max_lat, min_lat)).fetchall()
        truncated = len(found) > limit
        if truncated:
            x, y = (min_lon + max_lon) / 2, (min_lat + max_lat) / 2
            narrow = math.cos(math.radians(y))
            found = sorted(found, key=lambda r: (((r[2] - x) * narrow) ** 2 + (r[3] - y) ** 2, r[1]))[:limit]
        ids = sorted(r[0] for r in found)
        rows = []
        for start in range(0, len(ids), 500):
            some = ids[start:start + 500]
            rows += db.execute(f'SELECT zcta5, geometry FROM zcta WHERE rowid IN ({",".join("?" * len(some))})', some)
    features = [{'type': 'Feature', 'properties': {'zcta5': code}, 'geometry': json.loads(text)}
                for code, text in sorted(rows)]
    return {'type': 'FeatureCollection', 'truncated': truncated, 'features': features}


def states_geojson():
    """The state outlines as one GeoJSON FeatureCollection with properties {state, name, fips}."""
    return load(GEO / 'states.geojson')


def load_points():
    """(npi -> (lat, lon, precision), ccn -> (lat, lon, precision)); lat and lon are None when precision is none."""
    def table(name, key):
        return {r[key]: (float(r['lat']) if r['lat'] else None, float(r['lon']) if r['lon'] else None, r['geo_precision'])
                for r in read_csv(GEO / name)}
    return table('hcp_points.csv', 'npi'), table('center_points.csv', 'ccn')


# ---- addresses ----

def address(street, city, state, zip_code):
    """One address as compared and as sent: upper case, single spaces, five-digit ZIP."""
    def tidy(value):
        return ' '.join(str(value or '').upper().split())
    return tidy(street), tidy(city), tidy(state), re.sub(r'\D', '', str(zip_code or ''))[:5]


def choose_address(candidates, zip5, city, state):
    """(address, rule): the first listed with the expected ZIP, else with the expected city and state, else the first.

    Candidates are `address` tuples in file order; one with no street line cannot be geocoded and is passed over.
    """
    usable = [a for a in candidates if a[0]]
    _, city, state, zip5 = address('', city, state, zip5)
    rules = (('zip', lambda a: zip5 and a[3] == zip5), ('city_state', lambda a: city and a[1:3] == (city, state)))
    for rule, wanted in rules:
        chosen = next((a for a in usable if wanted(a)), None)
        if chosen:
            return chosen, rule
    return (usable[0], 'first') if usable else (None, 'none')


def practice_addresses(npis):
    """(NPI -> its practice addresses in file order, every (city, state, ZIP) of the file).

    The CMS Doctors and Clinicians file is streamed, never loaded whole; the second value feeds `town_centroids`.
    """
    found, places = defaultdict(list), set()
    with PRACTICES.open(newline='', encoding='utf-8-sig', errors='replace') as stream:
        reader = csv.reader(stream)
        at = {name: i for i, name in enumerate(next(reader))}
        npi, street, city, state, zip_code = (at[name] for name in ('NPI', 'adr_ln_1', 'City/Town', 'State', 'ZIP Code'))
        for row in reader:
            places.add((row[city], row[state], row[zip_code][:5]))
            if row[npi] in npis:
                found[row[npi]].append(address(row[street], row[city], row[state], row[zip_code]))
    return found, places


def hospital_addresses(ccns):
    """(CCN -> its enrolled addresses in file order, every (city, state, ZIP) of the CMS hospital enrollment file)."""
    found, places = defaultdict(list), set()
    with HOSPITALS.open(newline='', encoding='utf-8-sig', errors='replace') as stream:
        for row in csv.DictReader(stream):
            places.add((row['CITY'], row['STATE'], row['ZIP CODE']))
            if row['CCN'] in ccns:
                found[row['CCN']].append(address(row['ADDRESS LINE 1'], row['CITY'], row['STATE'], row['ZIP CODE']))
    return found, places


# ---- geocoder ----

def batch_payload(addresses):
    """The CSV body of one batch: a running row number, street, city, state and ZIP, and nothing else."""
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator='\n').writerows([number, *a] for number, a in enumerate(addresses, 1))
    return buffer.getvalue().encode('utf-8')


def parse_matches(text):
    """Row number -> (match, lon, lat) from a batch response; lon and lat are None unless the service gave a point.

    A response row is: id, the address as sent, Match / No_Match / Tie, then for a match Exact or Non_Exact, the
    matched address, "lon,lat", a TIGER line id and a street side.
    """
    found = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3 or not row[0].strip().isdigit():
            continue
        outcome = row[2].strip().lower()
        point = row[5].split(',') if outcome == 'match' and len(row) > 5 else []
        if len(point) == 2:
            found[row[0].strip()] = (row[3].strip().lower(), float(point[0]), float(point[1]))
        else:
            found[row[0].strip()] = ('tie' if outcome == 'tie' else 'no_match', None, None)
    return found


def post_batch(payload):
    """Send one batch to the Census Geocoder with the library's default client header; return (body, headers)."""
    response = requests.post(GEOCODER, files={'addressFile': ('addresses.csv', payload, 'text/csv')},
                             data={'benchmark': BENCHMARK}, timeout=(30, 1800))
    response.raise_for_status()
    return response.content, dict(response.headers)


def ask(chunk, send=post_batch):
    """Geocode one batch and freeze its request, response and retrieval record; return address -> match.

    A failed batch is retried up to TRIES times after a pause; a batch that keeps failing is halved. Messages name
    the row count and the kind of error only, never an address.
    """
    payload = batch_payload(chunk)
    for attempt in range(TRIES):
        started, clock = now(), time.monotonic()
        try:
            body, headers = send(payload)
            answers = parse_matches(body.decode('utf-8', errors='replace'))
            if not answers or not all(1 <= int(number) <= len(chunk) for number in answers):
                raise ValueError('the response is not a batch result')
            break
        except (requests.RequestException, ValueError) as error:
            print(f'geocoder batch of {len(chunk)} rows failed on try {attempt + 1}: {type(error).__name__}', flush=True)
            time.sleep(PAUSE)
    else:
        if len(chunk) <= SMALLEST:
            raise RuntimeError(f'The geocoder did not answer a batch of {len(chunk)} rows')
        half = len(chunk) // 2
        return {**ask(chunk[:half], send), **ask(chunk[half:], send)}
    key = 'geocoder_' + hashlib.sha256(payload).hexdigest()[:24]
    RAW_GEO.mkdir(parents=True, exist_ok=True)
    (RAW_GEO / f'{key}.request.csv').write_bytes(payload)
    saved = RAW_GEO / f'{key}.csv'
    saved.write_bytes(body)
    dump(RAW_GEO / f'{key}.meta.json', {
        'source_id': f'{MODULE}:{key}', 'module': MODULE, 'source_url': GEOCODER, 'method': 'POST', 'benchmark': BENCHMARK,
        'retrieval_started_at_utc': started, 'retrieved_at_utc': now(), 'seconds': round(time.monotonic() - clock, 1),
        'http_last_modified': headers.get('Last-Modified', ''), 'sha256': hashlib.sha256(body).hexdigest(),
        'bytes': len(body), 'rows_sent': len(chunk), 'rows_returned': len(answers),
        'rows_matched': sum(m[1] is not None for m in answers.values()),
        'request_sha256': hashlib.sha256(payload).hexdigest(), 'request_bytes': len(payload),
        'local_path': str(saved.relative_to(ROOT)) if saved.is_relative_to(ROOT) else saved.name})
    return {a: answers.get(str(number), UNANSWERED) for number, a in enumerate(chunk, 1)}


def frozen():
    """Every address of a frozen batch -> (match, lon, lat); the request file maps row numbers back to addresses."""
    found = {}
    for record in sorted(RAW_GEO.glob('geocoder_*.meta.json')):
        key = record.name[:-len('.meta.json')]
        with (RAW_GEO / f'{key}.request.csv').open(newline='', encoding='utf-8') as stream:
            sent = {row[0]: tuple(row[1:5]) for row in csv.reader(stream)}
        answers = parse_matches((RAW_GEO / f'{key}.csv').read_text(encoding='utf-8', errors='replace'))
        found.update({a: answers.get(number, UNANSWERED) for number, a in sent.items()})
    return found


def geocode(addresses, send=post_batch):
    """address -> (match, lon, lat) for every distinct address: frozen answers first, then one batch at a time."""
    found = frozen()
    pending = sorted(set(addresses) - set(found))
    for start in range(0, len(pending), BATCH):
        time.sleep(GAP if start else 0)
        found.update(ask(pending[start:start + BATCH], send))
    return {a: found[a] for a in set(addresses)}


# ---- points ----

def inside(lon, lat, box, margin=0.0):
    """Whether a point lies in (min_lon, min_lat, max_lon, max_lat) widened by `margin` degrees on every side."""
    return box[0] - margin <= lon <= box[2] + margin and box[1] - margin <= lat <= box[3] + margin


def town_centroids(places, zcta):
    """(CITY, STATE) -> (lon, lat), the mean centroid of the distinct ZCTAs listed with that city and state.

    `places` are (city, state, ZIP) triples as found in the published tables and in the two address files.
    """
    codes = defaultdict(set)
    for place in set(places):
        _, city, state, zip5 = address('', *place)
        if city and zip5 in zcta:
            codes[city, state].add(zip5)
    return {town: (sum(zcta[c][4] for c in some) / len(some), sum(zcta[c][5] for c in some) / len(some))
            for town, some in codes.items()}


def locate(match, zip5, city, state, zcta, towns, state_boxes):
    """(lat, lon, geo_precision) by the first rule that applies.

    `zcta` maps a ZIP to (min_lon, min_lat, max_lon, max_lat, centroid_lon, centroid_lat). The geocoded point is
    accepted inside the expected ZCTA's box widened by MARGIN, or inside the expected state's box when the ZIP has
    no ZCTA; then come the ZCTA centroid, the city-and-state centroid, and none.
    """
    _, city, state, zip5 = address('', city, state, zip5)
    _, lon, lat = match
    if lon is not None:
        if zip5 in zcta:
            accepted = inside(lon, lat, zcta[zip5], MARGIN)
        else:
            accepted = state in state_boxes and inside(lon, lat, state_boxes[state])
        if accepted:
            return lat, lon, 'address'
    if zip5 in zcta:
        return zcta[zip5][5], zcta[zip5][4], 'zip_centroid'
    if (city, state) in towns:
        return towns[city, state][1], towns[city, state][0], 'city_state_centroid'
    return None, None, 'none'


def point_rows(key, places, candidates, matches, zcta, towns, state_boxes):
    """One output row per place, and how many places each address rule chose.

    `places` are rows with the key, city, state and zip5; `candidates` maps a key to its `address` tuples and
    `matches` an address to its geocoder answer.
    """
    rows, rules = [], Counter()
    for place in places:
        chosen, rule = choose_address(candidates.get(place[key], []), place['zip5'], place['city'], place['state'])
        rules[rule] += 1
        match = matches[chosen] if chosen else ('no_address', None, None)
        lat, lon, precision = locate(match, place['zip5'], place['city'], place['state'], zcta, towns, state_boxes)
        rows.append({key: place[key], 'lat': '' if lat is None else f'{lat:.6f}',
                     'lon': '' if lon is None else f'{lon:.6f}', 'geo_precision': precision, 'geocoder_match': match[0]})
    return rows, dict(rules)


def chosen_addresses(key, places, candidates):
    """The distinct addresses to geocode: the one `choose_address` picks for each place that has any."""
    picks = (choose_address(candidates.get(p[key], []), p['zip5'], p['city'], p['state'])[0] for p in places)
    return {a for a in picks if a}


# ---- build ----

def build_states(source):
    """Write states.geojson from the frozen Census state file; return state -> bounding box."""
    shapes = sorted(read_shapes(source), key=lambda s: s[0]['STATEFP'])
    features = [{'type': 'Feature', 'properties': {'state': named['STUSPS'], 'name': named['NAME'], 'fips': named['STATEFP']},
                 'geometry': json.loads(geometry_json(geom))} for (named, _), geom in zip(shapes, snap(g for _, g in shapes))]
    text = json.dumps({'type': 'FeatureCollection', 'features': features}, separators=(',', ':'), ensure_ascii=False)
    (GEO / 'states.geojson').write_text(text, encoding='utf-8')
    return state_boxes()


def state_boxes():
    """State code -> (min_lon, min_lat, max_lon, max_lat) of its outline in states.geojson."""
    return {f['properties']['state']: shape(f['geometry']).bounds for f in states_geojson()['features']}


def build_zcta(source, sha256):
    """Write zcta.sqlite from the frozen Census ZCTA file unless it was already built from it at this tolerance."""
    path = GEO / 'zcta.sqlite'
    facts = {'source_sha256': sha256, 'tolerance_degrees': TOLERANCE, 'decimals': DIGITS}
    if path.exists():
        with closing(connect()) as db:
            built = dict(db.execute('SELECT key, value FROM facts'))
        if all(built.get(k) == str(v) for k, v in facts.items()):
            return
    shapes = read_shapes(source)
    originals = [geom for _, geom in shapes]
    centres = shapely.get_coordinates(shapely.centroid(np.asarray(originals, dtype=object)))
    records = [(named['ZCTA5CE20'], geom, round(float(lon), 6), round(float(lat), 6))
               for (named, _), geom, (lon, lat) in zip(shapes, simplify(originals), centres)]
    write_zcta(path, records, {**facts, 'rows': len(records), 'built_at_utc': now()})


def zcta_lookup():
    """ZIP -> (min_lon, min_lat, max_lon, max_lat, centroid_lon, centroid_lat) for every ZCTA."""
    with closing(connect()) as db:
        return {row[0]: row[1:] for row in db.execute(
            'SELECT zcta5, min_lon, min_lat, max_lon, max_lat, centroid_lon, centroid_lat FROM zcta')}


def source_record(path, role):
    """A local input's manifest entry: the URL and retrieval time of its download record, its bytes and SHA-256."""
    beside = path.with_name(path.name + '.meta.json')
    known = load(beside) if beside.exists() else {}
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'role': role, 'source_url': known.get('source_url', ''), 'retrieved_at_utc': known.get('retrieved_at_utc', ''),
            'bytes': path.stat().st_size, 'sha256': digest, 'local_path': str(path.relative_to(ROOT))}


def fetched(record, role):
    """A frozen download's manifest entry, from its retrieval record."""
    kept = ('source_url', 'retrieved_at_utc', 'http_last_modified', 'bytes', 'sha256', 'local_path')
    return {'role': role, **{k: record[k] for k in kept}}


def summary(rows, rules):
    """Counts for the manifest: by precision, by geocoder answer, by address rule, and distinct coordinates."""
    matched_elsewhere = sum(r['geocoder_match'] in ('exact', 'non_exact') and r['geo_precision'] != 'address' for r in rows)
    return {'rows': len(rows), 'by_geo_precision': {p: sum(r['geo_precision'] == p for r in rows) for p in PRECISIONS},
            'by_geocoder_match': dict(sorted(Counter(r['geocoder_match'] for r in rows).items())),
            'geocoded_but_not_accepted': matched_elsewhere, 'by_address_rule': rules,
            'distinct_coordinates': len({(r['lat'], r['lon']) for r in rows if r['lat']})}


def main():
    missing = [str(path.relative_to(ROOT)) for path in (HOSPITALS, PRACTICES) if not path.exists()]
    if missing:
        raise SystemExit('The geography step needs two CMS address files that are not on this machine: '
                         + ', '.join(missing) + '. See "The two address files" in agent/USAGE.md.')
    started = time.monotonic()
    GEO.mkdir(parents=True, exist_ok=True)
    states_file, states_record = fetch(STATES_URL, MODULE, suffix='.zip')
    zcta_file, zcta_record = fetch(ZCTA_URL, MODULE, suffix='.zip')
    boxes = build_states(states_file)
    build_zcta(zcta_file, zcta_record['sha256'])
    zcta = zcta_lookup()
    centers, hcps = read_csv(CENTERS), read_csv(UNIVERSE)
    hospital, hospital_places = hospital_addresses({c['ccn'] for c in centers})
    practice, practice_places = practice_addresses({h['npi'] for h in hcps})
    listed = {(p['city'], p['state'], p['zip5']) for p in centers + hcps}
    towns = town_centroids(listed | hospital_places | practice_places, zcta)
    matches = geocode(chosen_addresses('ccn', centers, hospital))
    matches.update(geocode(chosen_addresses('npi', hcps, practice)))
    center_rows, center_rules = point_rows('ccn', centers, hospital, matches, zcta, towns, boxes)
    hcp_rows, hcp_rules = point_rows('npi', hcps, practice, matches, zcta, towns, boxes)
    write_csv(GEO / 'center_points.csv', center_rows, ['ccn', *POINT_FIELDS])
    write_csv(GEO / 'hcp_points.csv', hcp_rows, ['npi', *POINT_FIELDS])
    batches = sorted((load(p) for p in RAW_GEO.glob('geocoder_*.meta.json')), key=lambda b: b['retrieved_at_utc'])
    counts = ('benchmark', 'rows_sent', 'rows_returned', 'rows_matched', 'seconds')
    manifest = {
        'built_at_utc': now(), 'build_seconds': round(time.monotonic() - started, 1),
        'sources': [fetched(states_record, 'state outlines, Census cartographic boundary file 1:20,000,000'),
                    fetched(zcta_record, 'ZCTA outlines, Census 2020 cartographic boundary file 1:500,000'),
                    *[{**fetched(b, 'Census Geocoder batch response'), **{k: b[k] for k in counts}} for b in batches],
                    source_record(HOSPITALS, 'center addresses, CMS hospital enrollment file'),
                    source_record(PRACTICES, 'practice addresses, CMS Doctors and Clinicians file'),
                    source_record(CENTERS, 'center list (published table)'),
                    source_record(UNIVERSE, 'clinician list (published table)')],
        'simplification': {'method': 'shapely.coverage_simplify over all ZCTAs, then rounding and repair',
                           'tolerance_degrees': TOLERANCE, 'decimals': DIGITS},
        'geocoding': {'benchmark': BENCHMARK, 'acceptance_margin_degrees': MARGIN, 'distinct_addresses': len(matches),
                      'distinct_addresses_matched': sum(m[1] is not None for m in matches.values())},
        'zcta_rows': len(zcta), 'state_features': len(boxes),
        'centers': summary(center_rows, center_rules), 'hcps': summary(hcp_rows, hcp_rules),
        'outputs': {name: {k: v for k, v in source_record(GEO / name, '').items() if k in ('bytes', 'sha256')}
                    for name in ('states.geojson', 'zcta.sqlite', 'center_points.csv', 'hcp_points.csv')}}
    dump(GEO / 'geo_manifest.json', manifest)
    print(json.dumps({k: manifest[k] for k in ('zcta_rows', 'state_features', 'centers', 'hcps', 'outputs')}, indent=1))


if __name__ == '__main__':
    main()
