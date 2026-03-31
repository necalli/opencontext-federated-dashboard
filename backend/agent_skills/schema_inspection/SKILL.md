---
name: Schema Inspection
description: Validate resource schema and field semantics before data querying.
---

Use this skill for field-level checks, schema explanations, and resource structure validation.

## Preferred tools
- `ckan__get_schema`
- `ckan__query_data`
- `ckan__get_dataset`
- `get_data`

## Guidance
- Inspect schema before running heavy data queries.
- Call out field types, nullability risks, and likely data-quality pitfalls.
- Keep examples and recommendations tied to specific resource IDs.
- Route by source: for NYC/Socrata and NYS/MTA schema discovery, use `get_data`; for Boston CKAN resources, use `ckan__get_schema` first.
