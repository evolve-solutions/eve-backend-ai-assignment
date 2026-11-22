# Corpus notice

`openlibrary-works.jsonl` holds 13,940 work records fetched from Open Library (https://openlibrary.org) on 2026-11-14 and processed into one JSON object per line.

## Licence status

Every record carries `licence_status = "unclear-no-explicit-licence"`. Open Library does not state an explicit licence for these work records; see https://openlibrary.org/developers/licensing for the terms Open Library publishes.

## Permitted use

Use this corpus only to complete or grade the Evolve Solutions backend + AI hiring exercise. Do not redistribute it or use it for any other purpose.

## Integrity

Verify the file before use:

```bash
sha256sum -c SHA256SUMS        # Linux
shasum -a 256 -c SHA256SUMS    # macOS
```
