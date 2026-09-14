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

    def test_json_source_object_is_normalized(self):
        payload = '''[{"id":"x1","title":"Flood","source":{"name":"Agency","source_id":"upstream-1","url":"https://example.org/x1"}}]'''
        out = parse_json(payload)
        self.assertEqual(out[0]["source"]["name"], "Agency")
        self.assertEqual(out[0]["source"]["source_id"], "upstream-1")
        self.assertEqual(out[0]["source"]["url"], "https://example.org/x1")

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
