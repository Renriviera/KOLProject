"""Address choice, geocoder batches, acceptance and fallbacks, the box query and simplification of the geography
layer, on invented addresses and coordinates only: no network and no data pack."""
import csv
import json
import math

import pytest
from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union

from kolproject.agent import geo

HERE = geo.address('12 Invented Way', 'Sampletown', 'ZZ', '00011-2222')
ELSEWHERE = geo.address('34 Made Up Road', 'Otherville', 'ZZ', '00022')
BLANK_STREET = geo.address('', 'Sampletown', 'ZZ', '00011')
# ZIP -> (min_lon, min_lat, max_lon, max_lat, centroid_lon, centroid_lat)
ZCTA = {'00011': (-100.2, 40.0, -100.0, 40.2, -100.1, 40.1), '00012': (-100.0, 40.0, -99.8, 40.2, -99.9, 40.1)}
STATES = {'ZZ': (-104.0, 37.0, -94.0, 43.0)}
TOWNS = {('SAMPLETOWN', 'ZZ'): (-100.0, 40.1)}


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(geo, 'GEO', tmp_path / 'geo')
    monkeypatch.setattr(geo, 'RAW_GEO', tmp_path / 'raw')
    monkeypatch.setattr(geo, 'PAUSE', 0)
    (tmp_path / 'geo').mkdir()
    return tmp_path


def test_address_is_upper_case_single_spaced_with_a_five_digit_zip():
    assert HERE == ('12 INVENTED WAY', 'SAMPLETOWN', 'ZZ', '00011')
    assert geo.address('  5  example   st ', None, 'zz', '000334444') == ('5 EXAMPLE ST', '', 'ZZ', '00033')


def test_choose_address_prefers_zip_then_city_and_state_then_first():
    assert geo.choose_address([ELSEWHERE, HERE], '00011', 'Otherville', 'ZZ') == (HERE, 'zip')
    assert geo.choose_address([HERE, ELSEWHERE], '00099', 'otherville', 'zz') == (ELSEWHERE, 'city_state')
    assert geo.choose_address([ELSEWHERE, HERE], '00099', 'Nowhere', 'ZZ') == (ELSEWHERE, 'first')
    assert geo.choose_address([ELSEWHERE, HERE], '', '', 'ZZ') == (ELSEWHERE, 'first')


def test_choose_address_passes_over_rows_without_a_street():
    assert geo.choose_address([BLANK_STREET, ELSEWHERE], '00011', 'Sampletown', 'ZZ') == (ELSEWHERE, 'first')
    assert geo.choose_address([BLANK_STREET], '00011', 'Sampletown', 'ZZ') == (None, 'none')
    assert geo.choose_address([], '00011', 'Sampletown', 'ZZ') == (None, 'none')


def test_batch_payload_sends_row_numbers_and_address_parts_only():
    rows = list(csv.reader(geo.batch_payload([HERE, ELSEWHERE]).decode().splitlines()))
    assert rows == [['1', *HERE], ['2', *ELSEWHERE]]


def test_parse_matches_reads_match_tie_and_no_match():
    text = ('"2","34 MADE UP ROAD, OTHERVILLE, ZZ, 00022","No_Match"\n'
            '"1","12 INVENTED WAY, SAMPLETOWN, ZZ, 00011","Match","Exact","12 INVENTED WAY, SAMPLETOWN, ZZ, 00011","-100.1,40.1","123","L"\n'
            '"3","5 EXAMPLE ST, , ZZ, 00033","Tie"\n'
            '"4","7 SAMPLE AVE, SAMPLETOWN, ZZ, 00011","Match","Non_Exact","7 SAMPLE AVE, SAMPLETOWN, ZZ, 00011","-100.15,40.05","124","R"\n'
            '<html>not a row</html>\n')
    assert geo.parse_matches(text) == {'1': ('exact', -100.1, 40.1), '2': ('no_match', None, None),
                                       '3': ('tie', None, None), '4': ('non_exact', -100.15, 40.05)}


def reply(payload, point='-100.1,40.1'):
    """A batch response that matches every odd row and misses every even one."""
    lines = []
    for number, *parts in csv.reader(payload.decode().splitlines()):
        sent = ', '.join(parts)
        lines.append(f'"{number}","{sent}","Match","Exact","{sent}","{point}","1","L"' if int(number) % 2 else f'"{number}","{sent}","No_Match"')
    return '\n'.join(lines).encode(), {'Last-Modified': ''}


def test_geocode_freezes_each_batch_and_never_asks_twice(folders):
    calls = []

    def send(payload):
        calls.append(payload)
        return reply(payload)
    first = geo.geocode({HERE, ELSEWHERE}, send)
    assert first == {HERE: ('exact', -100.1, 40.1), ELSEWHERE: ('no_match', None, None)}   # sent in sorted order
    assert len(calls) == 1 and b'INVENTED' in calls[0]
    record = json.loads(next((folders / 'raw').glob('geocoder_*.meta.json')).read_text())
    assert record['source_url'] == geo.GEOCODER and record['rows_sent'] == 2 and record['rows_matched'] == 1
    assert len(record['sha256']) == 64 and record['bytes'] > 0 and record['retrieved_at_utc'].endswith('Z')
    third = geo.address('56 Fictional Blvd', 'Sampletown', 'ZZ', '00011')
    again = geo.geocode({HERE, ELSEWHERE, third}, send)
    assert len(calls) == 2 and calls[1] == geo.batch_payload([third])    # only the new address goes out
    assert again[HERE] == first[HERE] and again[third] == ('exact', -100.1, 40.1)


def test_a_failing_batch_is_retried_then_halved(folders, monkeypatch):
    monkeypatch.setattr(geo, 'SMALLEST', 1)
    sizes = []

    def send(payload):
        sizes.append(len(payload.splitlines()))
        if sizes[-1] > 2:
            raise geo.requests.ConnectionError('no answer')
        return reply(payload)
    many = [geo.address(f'{n} Invented Way', 'Sampletown', 'ZZ', '00011') for n in range(1, 5)]
    found = geo.ask(many, send)
    assert sizes == [4, 4, 4, 2, 2] and set(found) == set(many)

    def junk(payload):
        return b'<html>Service unavailable</html>', {}
    with pytest.raises(RuntimeError):
        geo.ask(many[:1], junk)


def test_inside_and_margin():
    assert geo.inside(-100.1, 40.1, ZCTA['00011'])
    assert not geo.inside(-99.9, 40.1, ZCTA['00011']) and geo.inside(-99.9, 40.1, ZCTA['00011'], 0.25)
    assert not geo.inside(-100.1, 40.5, ZCTA['00011'], 0.25)


def test_locate_accepts_a_point_near_the_expected_zcta_only():
    place = ('00011', 'Sampletown', 'ZZ', ZCTA, TOWNS, STATES)
    assert geo.locate(('exact', -99.8, 40.3), *place) == (40.3, -99.8, 'address')
    assert geo.locate(('exact', -98.0, 40.1), *place) == (40.1, -100.1, 'zip_centroid')      # in the state, far from the ZIP
    assert geo.locate(('no_match', None, None), *place) == (40.1, -100.1, 'zip_centroid')
    assert geo.locate(('tie', None, None), *place) == (40.1, -100.1, 'zip_centroid')


def test_locate_without_a_zcta_uses_the_state_box_then_the_town_then_none():
    assert geo.locate(('non_exact', -98.0, 41.0), '00099', 'Sampletown', 'ZZ', ZCTA, TOWNS, STATES) == (41.0, -98.0, 'address')
    assert geo.locate(('exact', -80.0, 41.0), '00099', 'sampletown', 'zz', ZCTA, TOWNS, STATES) == (40.1, -100.0, 'city_state_centroid')
    assert geo.locate(('exact', -98.0, 41.0), '', 'Sampletown', 'XX', ZCTA, TOWNS, STATES) == (None, None, 'none')
    assert geo.locate(('no_address', None, None), '', '', 'ZZ', ZCTA, TOWNS, STATES) == (None, None, 'none')


def test_town_centroids_average_distinct_zcta_centroids():
    places = [('Sampletown', 'ZZ', '00011'), ('SAMPLETOWN ', 'zz', '000113333'), ('Sampletown', 'ZZ', '00012'),
              ('Sampletown', 'ZZ', '00099'), ('', 'ZZ', '00011')]
    towns = geo.town_centroids(places, ZCTA)
    assert list(towns) == [('SAMPLETOWN', 'ZZ')]
    assert towns['SAMPLETOWN', 'ZZ'] == pytest.approx((-100.0, 40.1))


def test_point_rows_share_coordinates_at_one_address_and_blank_when_unknown():
    places = [{'npi': '1000000001', 'city': 'Sampletown', 'state': 'ZZ', 'zip5': '00011'},
              {'npi': '1000000002', 'city': 'Sampletown', 'state': 'ZZ', 'zip5': '00011'},
              {'npi': '1000000003', 'city': 'Sampletown', 'state': 'ZZ', 'zip5': '00012'},
              {'npi': '1000000004', 'city': '', 'state': 'XX', 'zip5': ''}]
    candidates = {'1000000001': [ELSEWHERE, HERE], '1000000002': [HERE], '1000000004': [BLANK_STREET]}
    matches = {HERE: ('exact', -100.123456789, 40.123456789)}
    rows, rules = geo.point_rows('npi', places, candidates, matches, ZCTA, TOWNS, STATES)
    assert rules == {'zip': 2, 'none': 2}
    assert rows[0] == {'npi': '1000000001', 'lat': '40.123457', 'lon': '-100.123457', 'geo_precision': 'address', 'geocoder_match': 'exact'}
    assert (rows[1]['lat'], rows[1]['lon']) == (rows[0]['lat'], rows[0]['lon'])
    assert rows[2] == {'npi': '1000000003', 'lat': '40.100000', 'lon': '-99.900000', 'geo_precision': 'zip_centroid', 'geocoder_match': 'no_address'}
    assert rows[3] == {'npi': '1000000004', 'lat': '', 'lon': '', 'geo_precision': 'none', 'geocoder_match': 'no_address'}
    assert geo.chosen_addresses('npi', places, candidates) == {HERE}


def wavy(x0, x1, y0=0.0, y1=1.0, ripple=0.00002, steps=400):
    """A tile between x0 and x1 whose top and bottom edges ripple by far less than the tolerance."""
    xs = [x0 + (x1 - x0) * i / steps for i in range(steps + 1)]
    bottom = [(x, y0 + ripple * math.sin(i)) for i, x in enumerate(xs)]
    top = [(x, y1 + ripple * math.sin(i)) for i, x in enumerate(xs)]
    return Polygon(bottom + top[::-1])


def test_simplify_keeps_polygons_valid_on_the_grid_and_borders_shared():
    # A shared wiggly border: both tiles carry the same vertices along x = -99.5.
    seam = [(-99.5 + 0.0001 * math.sin(i), i / 200) for i in range(1, 200)]
    left = Polygon([(-100.0, 0.0), (-99.5, 0.0), *seam, (-99.5, 1.0), (-100.0, 1.0)])
    right = Polygon([(-99.5, 0.0), (-99.0, 0.0), (-99.0, 1.0), (-99.5, 1.0), *seam[::-1]])
    simple = geo.simplify([left, right], 0.01)
    assert all(g.is_valid and not g.is_empty and g.geom_type in ('Polygon', 'MultiPolygon') for g in simple)
    assert sum(len(g.exterior.coords) for g in simple) < (len(left.exterior.coords) + len(right.exterior.coords)) / 4
    assert all(round(v, 5) == v for g in simple for xy in g.exterior.coords for v in xy)
    assert unary_union(simple).area == pytest.approx(simple[0].area + simple[1].area, abs=1e-9)   # no overlap
    assert unary_union(simple).area == pytest.approx(1.0, abs=1e-3) and len(unary_union(simple).interiors) == 0   # no gap
    assert all(g.exterior.is_ccw for g in simple)
    one = geo.simplify([wavy(0.0, 1.0)], 0.001)[0]
    assert one.is_valid and len(one.exterior.coords) < 100 and one.area == pytest.approx(1.0, abs=1e-3)


def test_snap_repairs_a_polygon_that_rounding_breaks_and_keeps_holes():
    # The notch tip sits a hair inside the bottom edge; rounding to five decimals pushes it onto that edge.
    pinched = Polygon([(0, 0), (1, 0), (2, 0), (2, 1), (1, 0.000004), (0, 1)])
    ring = Polygon([(0, 0), (3, 0), (3, 3), (0, 3)], [[(1, 1), (2, 1), (2, 2), (1, 2)]])
    fixed, kept = geo.snap([pinched, ring])
    assert fixed.is_valid and fixed.geom_type in ('Polygon', 'MultiPolygon') and fixed.area == pytest.approx(pinched.area, abs=1e-4)
    assert kept.is_valid and len(kept.interiors) == 1 and kept.area == pytest.approx(8.0)
    text = geo.geometry_json(geo.snap([Polygon([(0.1234567, 0), (1, 0.9876543), (0, 1)])])[0])
    assert text == '{"type":"Polygon","coordinates":[[[0.12346,0.0],[1.0,0.98765],[0.0,1.0],[0.12346,0.0]]]}'
    assert ' ' not in text and shape(json.loads(text)).is_valid


def grid(folders, side=6):
    """A side-by-side grid of one-degree squares named by column and row, written as a ZCTA table."""
    records = [(f'{column}{row:04d}', box(column, row, column + 1, row + 1), column + 0.5, row + 0.5)
               for column in range(side) for row in range(side)]
    geo.write_zcta(folders / 'geo' / 'zcta.sqlite', records, {'tolerance_degrees': 0.5})
    return records


def test_zcta_features_returns_what_meets_the_box(folders):
    grid(folders)
    found = geo.zcta_features(1.2, 1.2, 2.8, 1.8)
    assert found['type'] == 'FeatureCollection' and found['truncated'] is False
    assert [f['properties'] for f in found['features']] == [{'zcta5': '10001'}, {'zcta5': '20001'}]
    assert shape(found['features'][0]['geometry']).equals(box(1, 1, 2, 2))
    assert geo.zcta_features(50, 50, 51, 51) == {'type': 'FeatureCollection', 'truncated': False, 'features': []}
    assert len(geo.zcta_features(-10, -10, 10, 10)['features']) == 36
    assert json.loads(json.dumps(found)) == found


def test_zcta_features_truncates_to_the_nearest_the_box_centre(folders):
    grid(folders)
    found = geo.zcta_features(-10, -10, 16, 16, limit=4)     # centre (3, 3): the four squares around it
    assert found['truncated'] is True
    assert sorted(f['properties']['zcta5'] for f in found['features']) == ['20002', '20003', '30002', '30003']
    assert geo.zcta_features(-10, -10, 16, 16, limit=36)['truncated'] is False


def test_zcta_table_has_the_agreed_columns_and_lookup(folders):
    grid(folders, side=2)
    with geo.closing(geo.connect()) as db:
        columns = [(row[1], row[2], row[5]) for row in db.execute('PRAGMA table_info(zcta)')]
        text = db.execute("SELECT geometry FROM zcta WHERE zcta5 = '10001'").fetchone()[0]
    assert columns == [('zcta5', 'TEXT', 1), ('min_lon', 'REAL', 0), ('min_lat', 'REAL', 0), ('max_lon', 'REAL', 0),
                       ('max_lat', 'REAL', 0), ('centroid_lon', 'REAL', 0), ('centroid_lat', 'REAL', 0), ('geometry', 'TEXT', 0)]
    assert json.loads(text)['type'] == 'Polygon'
    assert geo.zcta_lookup()['10001'] == (1.0, 1.0, 2.0, 2.0, 1.5, 1.5)


def test_states_and_points_load_from_the_pack_folder(folders):
    states = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {'state': 'ZZ', 'name': 'Sample', 'fips': '99'},
                                                         'geometry': json.loads(geo.geometry_json(box(-104, 37, -94, 43)))}]}
    (folders / 'geo' / 'states.geojson').write_text(json.dumps(states), encoding='utf-8')
    assert geo.states_geojson() == states and geo.state_boxes() == {'ZZ': (-104.0, 37.0, -94.0, 43.0)}
    geo.write_csv(folders / 'geo' / 'hcp_points.csv', [
        {'npi': '1000000001', 'lat': '40.100000', 'lon': '-100.100000', 'geo_precision': 'address', 'geocoder_match': 'exact'},
        {'npi': '1000000004', 'lat': '', 'lon': '', 'geo_precision': 'none', 'geocoder_match': 'no_address'}], ['npi', *geo.POINT_FIELDS])
    geo.write_csv(folders / 'geo' / 'center_points.csv', [
        {'ccn': '012345', 'lat': '40.2', 'lon': '-100.2', 'geo_precision': 'zip_centroid', 'geocoder_match': 'tie'}], ['ccn', *geo.POINT_FIELDS])
    hcps, centers = geo.load_points()
    assert hcps == {'1000000001': (40.1, -100.1, 'address'), '1000000004': (None, None, 'none')}
    assert centers == {'012345': (40.2, -100.2, 'zip_centroid')}
