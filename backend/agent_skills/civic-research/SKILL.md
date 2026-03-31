---
name: civic-research
description: >
  End-to-end civic and government data research skill. Use this skill whenever a user
  wants to research, analyze, or explore a civic topic using government open data —
  including housing, crime, transit, health, education, environment, permits, budgets,
  311 complaints, or any other public-sector domain. Triggers on phrases like "research
  housing data", "analyze NYC crime trends", "show me transit ridership", "look into
  Boston health outcomes", "what does the data say about [civic topic]", or any request
  to investigate a government dataset category. Also triggers when the user asks for a
  report, dashboard, or analysis on any topic that is likely covered by municipal,
  state, or federal open data. Do NOT wait for the user to say "open data" or name a
  specific dataset — if the topic sounds like something a government might track, invoke
  this skill proactively.
---

# Civic Research Skill

You are running an end-to-end civic data research workflow. Your job is to go from a
topic name to a polished, evidence-backed research report with visualizations — all
driven by real government open data.

## Quick Reference

| Step | What you do |
|------|-------------|
| 1. Parse intent | Extract category, geography, time range |
| 2. Discover datasets | Search 1–3 relevant MCP data sources |
| 3. Inspect schemas | Understand field names, types, coverage |
| 4. Query & analyze | Pull summary stats, trends, breakdowns |
| 5. Visualize | Publish charts/maps to dashboard canvas |
| 6. Interpret | Write plain-language narrative with policy angle |
| 7. Report | Deliver structured markdown report |

---

## Step 1 — Parse Intent

Extract from the user's message:
- **Category**: the civic topic (housing, crime, transit, health, 311, permits, budget, environment, education, …)
- **Geography**: NYC (default if unspecified), NYS, Boston, or a specific borough/neighborhood
- **Time range**: explicit dates or "last N years"; if missing, use whatever the dataset covers
- **Angle**: any specific sub-question (e.g., "by borough", "year-over-year change", "hotspots")

If geography is ambiguous, default to NYC and note this in the report.

---

## Step 2 — Discover Datasets

Route to the correct MCP server based on geography:

| Geography | Primary tool | Secondary tool |
|-----------|-------------|----------------|
| NYC / New York City | `get_data` (nyc-opengov) | `mcp__opencontext__get_data` |
| NYS / New York State / MTA | `get_data` (nys-opengov) | — |
| Boston / CKAN | `mcp__opencontext__ckan__search_datasets` | `mcp__opencontext__ckan__get_dataset` |
| Unspecified | NYC first, note assumption | — |

Search for 2–4 dataset candidates using the category as keywords. For each candidate,
note the dataset name, ID/resource identifier, record count, update frequency, and
a one-sentence description of what it covers.

Pick the **best 1–2 datasets** for the analysis based on recency, coverage, and
relevance. If two datasets complement each other (e.g., one has locations, one has
demographics), use both.

---

## Step 3 — Inspect Schemas

Before querying, understand the data:
- Call `get_data` or `ckan__get_dataset` to retrieve a small sample (limit: 5–10 rows)
- Identify: key categorical fields, numeric measures, date/time fields, lat/lon fields
- Note the date range of the data, geographic granularity (city-wide, borough, zip, tract, point)
- Flag any obvious data quality issues (nulls, inconsistent codes, gaps in time)

This prevents you from writing queries against column names that don't exist.

---

## Step 4 — Query & Analyze

Run 3–5 targeted queries to extract meaningful findings. Good query types:

1. **Volume/totals**: How many records overall? Per year? Per category?
2. **Trend over time**: Group by year or month, count or sum a measure
3. **Geographic breakdown**: Group by borough, neighborhood, zip code
4. **Category breakdown**: Group by type, status, severity, or demographic
5. **Outliers/extremes**: Top 10 highest/lowest values, recent spikes

Use `$limit`, `$order`, `$group`, `$where`, and `$select` SoQL parameters for
nyc-opengov/nys-opengov. For CKAN/Boston, use `mcp__opencontext__get_data` with
appropriate filters.

Extract concrete numbers from each query result to use in the narrative.

---

## Step 5 — Visualize

For each significant finding, publish a visualization using `mcp__opencontext__create_visualization`.

Choose chart types based on what the data shows:

| Finding type | Chart type | Notes |
|-------------|------------|-------|
| Trend over time | `line` or `bar` | x-axis = date/year |
| Geographic distribution | `map` | use lat_key/lon_key, basemap='osm' |
| Category comparison | `bar` or `column` | sorted descending |
| Part-of-whole | `pie` | only if ≤7 categories |
| Two-variable relationship | `scatter` | label outlier points |

For maps: always set `chart_type="map"`, `lat_key` and `lon_key` to the correct
field names from the schema, and `chart_options={"basemap": "osm"}`. If the data
doesn't have lat/lon but has borough/neighborhood, use a bar chart instead.

Aim for **2–4 visualizations** per research session. Give each a clear, descriptive
title (e.g., "NYC Housing Complaints by Borough, 2019–2024").

---

## Step 6 — Interpret

Write a clear, plain-language interpretation of what the data shows. Structure your
thinking around:

- **What's the headline finding?** One sentence, the most important thing.
- **What patterns stand out?** Trends, geographic concentrations, anomalies.
- **What's surprising or counter-intuitive?** Things that challenge assumptions.
- **What are the limitations?** Reporting gaps, collection methodology, time lag.
- **What are the policy implications?** What might this suggest for city/state action?

Avoid statistical jargon. Write as if explaining to an engaged, intelligent non-expert
— a city council staffer, a journalist, or a concerned community member.

---

## Step 7 — Deliver the Report

Output a structured markdown report using this template exactly:

# [Category] Research Report: [Geography]
*Data through [date range] · Source: [dataset name(s)]*

## Overview
[2–3 sentences: what this report covers, what data was used, geographic/time scope]

## Key Findings
1. **[Finding headline]**: [1–2 sentences with specific numbers]
2. **[Finding headline]**: [1–2 sentences with specific numbers]
3. **[Finding headline]**: [1–2 sentences with specific numbers]

## Visualizations
[Reference each chart/map published to the dashboard: title + 1-sentence takeaway]

## Interpretation
[2–4 paragraphs synthesizing patterns, anomalies, and implications]

## Data Sources & Limitations
- **Dataset**: [name, URL/ID, record count, update frequency]
- **Time coverage**: [date range]
- **Limitations**: [data quality issues, gaps, caveats]

## Recommended Next Steps
- [Specific follow-up analysis suggestion]
- [Policy or operational question this data raises]
- [Additional dataset that would complement this analysis]

---

## Error Handling

- **Dataset not found**: Try alternate search terms (synonyms, abbreviations). If still nothing, say so and suggest manual search terms.
- **Query fails**: Re-check column names from schema inspection step. Try a simpler query first, then build up.
- **No lat/lon for map**: Fall back to a geographic bar chart (by borough or zip code).
- **Sparse data**: Note the limitation clearly; use what's available; never fabricate numbers.
- **Ambiguous geography**: Default to NYC, state that assumption explicitly in the Overview.

---

## Worked Example (internal reference — do not recite this to the user)

**User prompt**: "Research 311 noise complaints in NYC"

1. **Parse**: category=311/noise, geography=NYC, time=all available
2. **Discover**: NYC 311 Service Requests on nyc-opengov; pick the main 311 dataset
3. **Schema**: fields include `complaint_type`, `created_date`, `borough`, `latitude`, `longitude`, `status`
4. **Queries**:
   - Total noise complaints grouped by year
   - Breakdown by complaint_type (music, construction, vehicle, etc.)
   - Count by borough, ordered descending
   - Sample 1,000 geolocated records for map
5. **Visualize**: line chart (yearly trend), bar chart (by complaint type), map (point locations with OSM basemap)
6. **Interpret**: Brooklyn and Manhattan lead; late-night music peaks in summer; construction noise spikes weekday mornings
7. **Report**: full markdown output with section headers and viz references
