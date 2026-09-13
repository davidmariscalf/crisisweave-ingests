# crisisweave-ingests

Public ingestion adapters for CrisisWeave. This repository replaces the accidentally private `crisisweave-ingest` repository for public testing.

The MVP intentionally uses only the Python standard library so a responder can run it in a constrained environment without first installing a dependency tree.

## Supported inputs

- CAP 1.x XML alerts
- RSS 2.0 and Atom feeds
- Generic JSON arrays or `{ "items": [...] }` feeds

Every adapter emits the normalized CrisisWeave event contract defined in `davidmariscalf/CrisisWeave/schema/event.schema.json`.

## Quick start

```bash
python crisisweave_ingests.py cap examples/sample-cap.xml
python crisisweave_ingests.py rss examples/sample-rss.xml
python crisisweave_ingests.py json examples/sample.json
```

Output is newline-delimited JSON, which makes it easy to pipe into `crisisweave-verify` or save to disk.

## Design choices

The adapter never invents location coordinates. CAP polygons and rich geospatial normalization can be added later, but ambiguous text is preserved rather than guessed. Source identifiers and original payload fragments are retained for provenance.

## Safety

Ingestion does not imply verification. An event from an official CAP sender is marked `official=true`; RSS and generic JSON are not automatically promoted to official status.
