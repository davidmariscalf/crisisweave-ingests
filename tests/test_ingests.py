import math
import tempfile
import unittest
from pathlib import Path

from crisisweave_ingests import MAX_INPUT_BYTES, _read_input, parse_cap, parse_json, parse_rss


class IngestTests(unittest.TestCase):
    def test_atom_link_href_is_preserved(self):
        xml = '''<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>Agency feed</title>
          <entry>
            <id>abc</id>
            <title>Flood warning</title>
            <updated>2026-01-01T12:00:00Z</updated>
            <link href="https://example.org/incidents/abc" />
          </entry>
        </feed>'''
        out = parse_rss(xml)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["source"]["url"], "https://example.org/incidents/abc")
        self.assertEqual(out[0]["observed_at"], "2026-01-01T12:00:00+00:00")

    def test_json_source_object_is_normalized(self):
        payload = '''[{"id":"x1","title":"Flood","source":{"name":"Agency","source_id":"upstream-1","url":"https://example.org/x1"}}]'''
        out = parse_json(payload)
        self.assertEqual(out[0]["source"]["name"], "Agency")
        self.assertEqual(out[0]["source"]["source_id"], "upstream-1")
        self.assertEqual(out[0]["source"]["url"], "https://example.org/x1")

    def test_missing_or_invalid_timestamp_is_not_fabricated(self):
        missing = parse_json('[{"id":"x1","title":"Flood"}]')[0]
        invalid = parse_json('[{"id":"x2","title":"Flood","observed_at":"not-a-date"}]')[0]
        self.assertIsNone(missing["observed_at"])
        self.assertIsNone(invalid["observed_at"])
        self.assertEqual(invalid["raw"]["input_timestamp"], "not-a-date")

    def test_cap_invalid_expiry_is_not_replaced_with_now(self):
        xml = '''<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
          <identifier>cap1</identifier><sender>agency@example.org</sender><sent>2026-01-01T12:00:00Z</sent><status>Actual</status>
          <info><event>Flood</event><headline>Flood warning</headline><severity>Severe</severity><expires>invalid</expires></info>
        </alert>'''
        out = parse_cap(xml)
        self.assertEqual(out[0]["observed_at"], "2026-01-01T12:00:00+00:00")
        self.assertIsNone(out[0]["expires_at"])

    def test_cap_polygon_becomes_geojson_and_preserves_raw_area(self):
        xml = '''<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
          <identifier>cap-poly</identifier><sender>agency@example.org</sender><sent>2026-01-01T12:00:00Z</sent><status>Actual</status>
          <info><event>Flood</event><headline>Flood polygon</headline><severity>Severe</severity>
            <area><areaDesc>River district</areaDesc><polygon>40.0,-3.0 40.2,-3.0 40.2,-2.8</polygon></area>
          </info>
        </alert>'''
        event = parse_cap(xml)[0]
        self.assertEqual(event["area"], "River district")
        self.assertEqual(event["geometry"]["type"], "Polygon")
        ring = event["geometry"]["coordinates"][0]
        self.assertEqual(ring[0], [-3.0, 40.0])
        self.assertEqual(ring[0], ring[-1])
        self.assertEqual(event["raw"]["areas"][0]["polygon"][0], "40.0,-3.0 40.2,-3.0 40.2,-2.8")

    def test_cap_circle_becomes_closed_polygon(self):
        xml = '''<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
          <identifier>cap-circle</identifier><sender>agency@example.org</sender><sent>2026-01-01T12:00:00Z</sent><status>Actual</status>
          <info><event>Wildfire</event><headline>Evacuation radius</headline><severity>Extreme</severity>
            <area><areaDesc>Evacuation zone</areaDesc><circle>40.0,-3.0 10</circle></area>
          </info>
        </alert>'''
        event = parse_cap(xml)[0]
        self.assertEqual(event["geometry"]["type"], "Polygon")
        ring = event["geometry"]["coordinates"][0]
        self.assertEqual(len(ring), 65)
        self.assertEqual(ring[0], ring[-1])
        self.assertTrue(all(-180 <= point[0] <= 180 and -90 <= point[1] <= 90 for point in ring))

    def test_cap_multiple_areas_are_deterministic_multipolygon(self):
        xml = '''<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
          <identifier>cap-multi</identifier><sender>agency@example.org</sender><sent>2026-01-01T12:00:00Z</sent><status>Actual</status>
          <info><event>Storm</event><headline>Two warning zones</headline><severity>Moderate</severity>
            <area><areaDesc>Zone A</areaDesc><polygon>40,-3 40.1,-3 40.1,-2.9</polygon></area>
            <area><areaDesc>Zone B</areaDesc><polygon>41,-4 41.1,-4 41.1,-3.9</polygon><geocode><valueName>SAME</valueName><value>001</value></geocode></area>
          </info>
        </alert>'''
        event = parse_cap(xml)[0]
        self.assertEqual(event["area"], "Zone A; Zone B")
        self.assertEqual(event["geometry"]["type"], "MultiPolygon")
        self.assertEqual(len(event["geometry"]["coordinates"]), 2)
        self.assertEqual(event["geometry"]["coordinates"][0][0][0], [-3.0, 40.0])
        self.assertEqual(event["geometry"]["coordinates"][1][0][0], [-4.0, 41.0])
        self.assertEqual(event["raw"]["areas"][1]["geocode"], [{"valueName": "SAME", "value": "001"}])

    def test_cap_malformed_shape_is_rejected_without_dropping_alert(self):
        xml = '''<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
          <identifier>cap-bad</identifier><sender>agency@example.org</sender><sent>2026-01-01T12:00:00Z</sent><status>Actual</status>
          <info><event>Flood</event><headline>Mixed geometry</headline><severity>Severe</severity>
            <area><areaDesc>Known area</areaDesc>
              <polygon>999,-3 40,-3 40,-2</polygon>
              <circle>40,-3 not-a-radius</circle>
              <geocode><valueName>UGC</valueName><value>ABC123</value></geocode>
            </area>
          </info>
        </alert>'''
        event = parse_cap(xml)[0]
        self.assertEqual(event["id"], "cap-bad")
        self.assertEqual(event["area"], "Known area")
        self.assertIsNone(event["geometry"])
        self.assertEqual(event["raw"]["areas"][0]["polygon"], ["999,-3 40,-3 40,-2"])
        self.assertEqual(event["raw"]["areas"][0]["circle"], ["40,-3 not-a-radius"])
        self.assertEqual(event["raw"]["areas"][0]["geocode"][0]["value"], "ABC123")

    def test_unsafe_source_url_is_dropped(self):
        out = parse_json('[{"title":"Flood","source":{"name":"Agency","url":"javascript:alert(1)"}}]')
        self.assertIsNone(out[0]["source"]["url"])

    def test_invalid_coordinates_are_dropped(self):
        out = parse_json('[{"title":"Flood","geometry":{"type":"Point","coordinates":[999,999]}}]')
        self.assertIsNone(out[0]["geometry"])

    def test_invalid_scores_fall_back_to_bounded_defaults(self):
        out = parse_json('[{"title":"Flood","severity":5,"confidence":-2}]')
        self.assertEqual(out[0]["severity"], 0.5)
        self.assertEqual(out[0]["confidence"], 0.4)
        out = parse_json('[{"title":"Flood","severity":NaN,"confidence":Infinity}]')
        self.assertTrue(math.isfinite(out[0]["severity"]))
        self.assertTrue(math.isfinite(out[0]["confidence"]))

    def test_cap_without_identifier_still_has_source_id(self):
        xml = '''<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
          <sender>agency@example.org</sender><sent>2026-01-01T12:00:00Z</sent><status>Actual</status>
          <info><event>Flood</event><headline>Flood warning</headline><severity>Severe</severity></info>
        </alert>'''
        out = parse_cap(xml)
        self.assertTrue(out[0]["id"])
        self.assertEqual(out[0]["source"]["source_id"], out[0]["id"])

    def test_file_input_limit_is_enforced_before_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "huge.json"
            with path.open("wb") as handle:
                handle.truncate(MAX_INPUT_BYTES + 1)
            with self.assertRaises(ValueError):
                _read_input(str(path))


if __name__ == "__main__":
    unittest.main()
