---
name: Analysis and SQL
description: Execute safe SQL and aggregation workflows for analytical civic-data questions.
---

Use this skill when the user asks for grouped metrics, trends, or SQL-driven analysis.

## Preferred tools
- `ckan__aggregate_data`
- `ckan__execute_sql`
- `ckan__query_data`
- `ckan__get_schema`
- `get_data`

## Guidance
- Use `SELECT`-only SQL.
- Validate schema and field names before advanced queries.
- Explain filters, grouping keys, and aggregate functions.
- Explicitly call out assumptions and data quality caveats.
- Route by geography/source: for NYC/Socrata and NYS/MTA requests, prefer `get_data`; for Boston CKAN requests, prefer `ckan__*`.
