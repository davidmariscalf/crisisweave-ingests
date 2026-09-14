from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 50_000
MAX_TITLE = 500
MAX_DESCRIPTION = 20_000
MAX_AREA = 1000
MAX_SOURCE_NAME = 300
MAX_SOURCE_ID = 512
MAX_URL = 2048


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _bounded(value: Any, limit: int, default: str = "") -> str:
    text = str(value or default).strip()
    return text[:limit]


def _iso(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    value = value.strip()
    try:
        if value.endswith("Z"):
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        else:
            parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()


def _stable_id(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8", "ignore")
    return hashlib.sha256(payload).hexdigest()[:24]


def _kind(text: str) -> str:
    s = text.casefold()
    mapping = {
        "earthquake": ("earthquake", "seismic", "quake"),
        "flood": ("flood", "inundation", "flash flood"),
        "wildfire": ("wildfire", "forest fire", "bushfire"),
        "storm": ("storm", "hurricane", "cyclone", "tornado", "typhoon"),
        "landslide": ("landslide", "mudslide"),
        "medical": ("medical", "disease", "outbreak", "epidemic"),
        "security": ("security", "attack", "violence"),
        "infrastructure": ("power outage", "bridge", "road closure", "infrastructure"),
    }
    for kind, needles in mapping.items():
        if any(n in s for n in needles):
            return kind
    return "other"


def _severity(value: str | None) -> float:
    table = {
        "extreme": 1.0,
        "severe": 0.8,
        "moderate": 0.55,
        "minor": 0.3,
        "unknown": 0.5,
    }
    return table.get((value or "unknown").casefold(), 0.5)


def _score(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return default
    return number


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0", ""}:
            return False
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    return False


def _safe_url(value: Any, base: str | None = None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if base:
        raw = urljoin(base, raw)
    if len(raw) > MAX_URL:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password:
        return None
    return raw


def _point(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("type") != "Point":
        return None
    coords = value.get("coordinates")
    if not isinstance(coords, list) or len(coords) != 2:
        return None
    try:
        lon, lat = float(coords[0]), float(coords[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lon) or not math.isfinite(lat):
        return None
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        return None
    return {"type": "Point", "coordinates": [lon, lat]}


def _limit_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(events) > MAX_EVENTS:
        raise ValueError(f"feed exceeds {MAX_EVENTS} events")
    return events


def _entry_link(entry: ET.Element, base: str | None = None) -> str | None:
    for child in entry:
        if child.tag.split("}")[-1].casefold() != "link":
            continue
        candidate = _text(child) or child.attrib.get("href") or ""
        url = _safe_url(candidate, base)
        if url:
            return url
    return _safe_url(base)


def parse_cap(xml_text: str, source_url: str | None = None) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    identifier = _bounded(_text(root.find(f"{ns}identifier")), MAX_SOURCE_ID)
    sender = _bounded(_text(root.find(f"{ns}sender")) or "CAP source", MAX_SOURCE_NAME)
    sent = _text(root.find(f"{ns}sent"))
    status = _text(root.find(f"{ns}status"))
    safe_source_url = _safe_url(source_url)

    infos = root.findall(f"{ns}info") or [root]
    if len(infos) > MAX_EVENTS:
        raise ValueError(f"CAP feed exceeds {MAX_EVENTS} info blocks")
    events: list[dict[str, Any]] = []
    for idx, info in enumerate(infos):
        event_name = _bounded(_text(info.find(f"{ns}event")) or "CAP alert", MAX_TITLE)
        headline = _bounded(_text(info.find(f"{ns}headline")) or event_name, MAX_TITLE)
        description = _bounded(_text(info.find(f"{ns}description")), MAX_DESCRIPTION)
        severity = _text(info.find(f"{ns}severity"))
        effective = _text(info.find(f"{ns}effective")) or sent
        expires = _text(info.find(f"{ns}expires")) or None
        area_node = info.find(f"{ns}area")
        area = _bounded(_text(area_node.find(f"{ns}areaDesc")) if area_node is not None else "", MAX_AREA)
        event_id = identifier or _stable_id(sender, event_name, effective, str(idx))

        events.append(
            {
                "id": event_id,
                "kind": _kind(f"{event_name} {headline} {description}"),
                "title": headline,
                "description": description,
                "observed_at": _iso(effective),
                "expires_at": _iso(expires) if expires else None,
                "severity": _severity(severity),
                "confidence": 0.9 if status.casefold() == "actual" else 0.75,
                "official": True,
                "geometry": None,
                "area": area or None,
                "source": {
                    "name": sender,
                    "type": "cap",
                    "url": safe_source_url,
                    "source_id": event_id,
                },
                "evidence": [{"source": sender, "weight": 0.9, "note": "CAP alert"}],
                "raw": {
                    "status": _bounded(status, 64),
                    "severity": _bounded(severity, 64),
                    "event": event_name,
                },
                "tags": ["cap"],
            }
        )
    return events


def parse_rss(xml_text: str, source_url: str | None = None) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    entries: Iterable[ET.Element]
    if root.tag.casefold().endswith("rss"):
        channel = root.find("channel")
        entries = channel.findall("item") if channel is not None else []
        source_name = _bounded(_text(channel.find("title")) if channel is not None else "RSS source", MAX_SOURCE_NAME)
    else:
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"
        entries = root.findall(f"{ns}entry")
        source_name = _bounded(_text(root.find(f"{ns}title")) or "Atom source", MAX_SOURCE_NAME)

    entries = list(entries)
    if len(entries) > MAX_EVENTS:
        raise ValueError(f"syndication feed exceeds {MAX_EVENTS} entries")

    events: list[dict[str, Any]] = []
    for entry in entries:
        def find_any(*names: str) -> str:
            for name in names:
                for child in entry:
                    if child.tag.split("}")[-1].casefold() == name.casefold():
                        text = _text(child)
                        if text:
                            return text
            return ""

        title = _bounded(find_any("title") or "Feed report", MAX_TITLE)
        description = _bounded(find_any("description", "summary", "content"), MAX_DESCRIPTION)
        guid = _bounded(find_any("guid", "id"), MAX_SOURCE_ID)
        published = find_any("pubDate", "published", "updated")
        link = _entry_link(entry, source_url)
        sid = guid or link or _stable_id(source_name, title, published)
        sid = _bounded(sid, MAX_SOURCE_ID)
        events.append(
            {
                "id": _stable_id(source_name, sid),
                "kind": _kind(f"{title} {description}"),
                "title": title,
                "description": description,
                "observed_at": _iso(published),
                "expires_at": None,
                "severity": 0.5,
                "confidence": 0.45,
                "official": False,
                "geometry": None,
                "area": None,
                "source": {"name": source_name or "RSS source", "type": "rss", "url": link, "source_id": sid},
                "evidence": [{"source": source_name or "RSS source", "weight": 0.45, "note": "syndicated feed item"}],
                "raw": {"guid": guid, "published": _bounded(published, 128)},
                "tags": ["rss"],
            }
        )
    return events


def parse_json(payload: str, source_url: str | None = None) -> list[dict[str, Any]]:
    data = json.loads(payload)
    if isinstance(data, dict):
        items = data.get("items") or data.get("events") or data.get("features") or [data]
    else:
        items = data
    if not isinstance(items, list):
        raise ValueError("JSON input must be an array or contain items/events/features array")
    if len(items) > MAX_EVENTS:
        raise ValueError(f"JSON feed exceeds {MAX_EVENTS} records")

    default_url = _safe_url(source_url)
    events: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        props = item.get("properties") if isinstance(item.get("properties"), dict) else item
        title = _bounded(props.get("title") or props.get("name") or props.get("event") or "JSON report", MAX_TITLE)
        description = _bounded(props.get("description") or props.get("summary") or "", MAX_DESCRIPTION)

        raw_source = props.get("source")
        if isinstance(raw_source, dict):
            source_name = _bounded(raw_source.get("name") or "JSON source", MAX_SOURCE_NAME)
            source_id_value = raw_source.get("source_id") or raw_source.get("id")
            event_url = _safe_url(raw_source.get("url"), source_url) or default_url
        else:
            source_name = _bounded(raw_source or "JSON source", MAX_SOURCE_NAME)
            source_id_value = None
            event_url = default_url

        source_id = _bounded(
            source_id_value or props.get("id") or item.get("id") or _stable_id(title, str(idx)),
            MAX_SOURCE_ID,
        )
        geometry = _point(item.get("geometry"))
        observed = str(props.get("observed_at") or props.get("timestamp") or props.get("time") or "")
        severity = _score(props.get("severity"), 0.5)
        confidence = _score(props.get("confidence"), 0.4)
        official = _bool(props.get("official", False))
        area = _bounded(props.get("area"), MAX_AREA) or None

        events.append(
            {
                "id": _stable_id(source_name, source_id),
                "kind": _kind(f"{title} {description}"),
                "title": title,
                "description": description,
                "observed_at": _iso(observed),
                "expires_at": None,
                "severity": severity,
                "confidence": confidence,
                "official": official,
                "geometry": geometry,
                "area": area,
                "source": {"name": source_name or "JSON source", "type": "json", "url": event_url, "source_id": source_id},
                "evidence": [{"source": source_name or "JSON source", "weight": confidence, "note": "generic JSON feed"}],
                "raw": {
                    "input_id": _bounded(props.get("id") or item.get("id"), MAX_SOURCE_ID),
                    "input_timestamp": _bounded(observed, 128),
                },
                "tags": ["json"],
            }
        )
    return _limit_events(events)


def _read_input(path: str) -> str:
    if path == "-":
        text = sys.stdin.read(MAX_INPUT_BYTES + 1)
        if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError(f"stdin exceeds {MAX_INPUT_BYTES} bytes")
        return text
    input_path = Path(path)
    if input_path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"input exceeds {MAX_INPUT_BYTES} bytes")
    return input_path.read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize crisis feeds into CrisisWeave JSONL")
    parser.add_argument("format", choices=["cap", "rss", "json"])
    parser.add_argument("path", help="Path to local input file, or '-' for stdin")
    parser.add_argument("--source-url", default=None)
    args = parser.parse_args()

    text = _read_input(args.path)
    fn = {"cap": parse_cap, "rss": parse_rss, "json": parse_json}[args.format]
    for event in fn(text, args.source_url):
        print(json.dumps(event, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
