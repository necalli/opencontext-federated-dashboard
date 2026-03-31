---
name: Dataset Discovery
description: Discover relevant civic datasets and summarize fitness for the user objective.
---

Use this skill when the user asks to find or shortlist datasets.

## Preferred tools
- `ckan__search_datasets`
- `ckan__get_dataset`
- `get_data`

## Guidance
- Start broad, then narrow with specific domain terms.
- Return dataset names, IDs, and concise fit-for-purpose notes.
- Flag stale, legacy, or low-quality datasets when metadata indicates risk.
- Route by source: NYC/Socrata and NYS/MTA requests should prefer `get_data`; Boston requests should prefer `ckan__search_datasets` / `ckan__get_dataset`.
