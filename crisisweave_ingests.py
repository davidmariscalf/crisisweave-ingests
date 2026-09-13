from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _iso(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    value = value.strip()
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1] + "+00:00").isoformat()
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
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


def parse_cap(xml_text: str, source_url: str | None = None) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    identifier = _text(root.find(f"{ns}identifier"))
    sender = _text(root.find(f"{ns}sender")) or "CAP source"
    sent = _text(root.find(f"{ns}sent"))
    status = _text(root.find(f"{ns}status"))

    infos = root.findall(f"{ns}info") or [root]
    events: list[dict[str, Any]] = []
    for idx, info in enumerate(infos):
        event_name = _text(info.find(f"{ns}event")) or "CAP alert"
        headline = _text(info.find(f"{ns}headline")) or event_name
        description = _text(info.find(f"{ns}description"))
        severity = _text(info.find(f"{ns}severity"))
        effective = _text(info.find(f"{ns}effective")) or sent
        expires = _text(info.find(f"{ns}expires")) or None
        area_node = info.find(f"{ns}area")
        area = _text(area_node.find(f"{ns}areaDesc")) if area_node is not None else ""

        events.append(
            {
                "id": identifier or _stable_id(sender, event_name, effective, str(idx)),
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
                    "url": source_url,
                    "source_id": identifier or None,
                },
                "evidence": [{"source": sender, "weight": 0.9, "note": "CAP alert"}],
                "raw": {"status": status, "severity": severity, "event": event_name},
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
        source_name = _text(channel.find("title")) if channel is not None else "RSS source"
    else:
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"
        entries = root.findall(f"{ns}entry")
        source_name = _text(root.find(f"{ns}title")) or "Atom source"

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

        title = find_any("title") or "Feed report"
        description = find_any("description", "summary", "content")
        guid = find_any("guid", "id")
        published = find_any("pubDate", "published", "updated")
        link = find_any("link") or source_url
        sid = guid or link or _stable_id(source_name, title, published)
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
                "source": {"name": source_name, "type": "rss", "url": link, "source_id": sid},
                "evidence": [{"source": source_name, "weight": 0.45, "note": "syndicated feed item"}],
                "raw": {"guid": guid, "published": published},
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

    events: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        props = item.get("properties") if isinstance(item.get("properties"), dict) else item
        title = str(props.get("title") or props.get("name") or props.get("event") or "JSON report")
        description = str(props.get("description") or props.get("summary") or "")
        source_name = str(props.get("source") or "JSON source")
        source_id = str(props.get("id") or item.get("id") or _stable_id(title, str(idx)))
        geometry = item.get("geometry") if isinstance(item.get("geometry"), dict) else None
        if geometry and geometry.get("type") != "Point":
            geometry = None
        events.append(
            {
                "id": _stable_id(source_name, source_id),
                "kind": _kind(f"{title} {description}"),
                "title": title,
                "description": description,
                "observed_at": _iso(str(props.get("observed_at") or props.get("timestamp") or props.get("time") or "")),
                "expires_at": None,
                "severity": float(props.get("severity", 0.5)) if isinstance(props.get("severity", 0.5), (int, float)) else 0.5,
                "confidence": float(props.get("confidence", 0.4)) if isinstance(props.get("confidence", 0.4), (int, float)) else 0.4,
                "official": bool(props.get("official", False)),
                "geometry": geometry,
                "area": props.get("area"),
                "source": {"name": source_name, "type": "json", "url": source_url, "source_id": source_id},
                "evidence": [{"source": source_name, "weight": 0.4, "note": "generic JSON feed"}],
                "raw": item,
                "tags": ["json"],
            }
        )
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize crisis feeds into CrisisWeave JSONL")
    parser.add_argument("format", choices=["cap", "rss", "json"])
    parser.add_argument("path", help="Path to local input file, or '-' for stdin")
    parser.add_argument("--source-url", default=None)
    args = parser.parse_args()

    text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
    fn = {"cap": parse_cap, "rss": parse_rss, "json": parse_json}[args.format]
    for event in fn(text, args.source_url):
        print(json.dumps(event, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
