import { useEffect, useMemo, useState } from 'react'
import { CircleMarker, MapContainer, TileLayer, Tooltip as MapTooltip, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'

const LAYOUT_KEY = 'opencontext-canvas-layout'
const PALETTE = ['#6bc8ff', '#8de2a3', '#f8c66d', '#d58bff', '#ff9c6b', '#76e4d3']
const LAT_KEY_HINTS = [
  'latitude',
  'lat',
  'y_coord',
  'ycoord',
  'lat_dd',
  'geo_lat',
  'incident_latitude',
  'location_latitude',
]
const LON_KEY_HINTS = [
  'longitude',
  'lon',
  'lng',
  'x_coord',
  'xcoord',
  'lon_dd',
  'geo_lon',
  'incident_longitude',
  'location_longitude',
]

function toNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value !== 'string') return null
  const trimmed = value.trim().replace(/,/g, '')
  if (!trimmed) return null
  const direct = Number(trimmed)
  if (Number.isFinite(direct)) return direct
  const match = trimmed.match(/-?\d+(?:\.\d+)?/)
  if (!match) return null
  const parsed = Number(match[0])
  return Number.isFinite(parsed) ? parsed : null
}

function deriveKeys(records, explicitX, explicitY) {
  const first = records[0] && typeof records[0] === 'object' ? records[0] : {}
  const keys = Object.keys(first)
  const yCandidate =
    (explicitY && keys.includes(explicitY) ? explicitY : '') ||
    keys.find((key) => records.some((row) => toNumber(row?.[key]) !== null)) ||
    ''
  const xCandidate =
    (explicitX && keys.includes(explicitX) ? explicitX : '') ||
    keys.find((key) => key !== yCandidate) ||
    keys[0] ||
    ''
  return { xKey: xCandidate, yKey: yCandidate }
}

function buildSeriesGroups(records, xKey, yKey, seriesKey, limit = 24) {
  const rows = Array.isArray(records) ? records.slice(0, limit) : []
  const hasSeries =
    seriesKey &&
    rows.some((row) => {
      const value = String(row?.[seriesKey] ?? '').trim()
      return Boolean(value)
    })

  if (!hasSeries) {
    const points = rows
      .map((row, idx) => {
        const y = toNumber(row?.[yKey])
        if (y === null) return null
        return {
          index: idx,
          x: String(row?.[xKey] ?? `Point ${idx + 1}`),
          y,
        }
      })
      .filter(Boolean)
    return [{ name: yKey || 'value', points }]
  }

  const grouped = new Map()
  rows.forEach((row, idx) => {
    const y = toNumber(row?.[yKey])
    if (y === null) return
    const groupName = String(row?.[seriesKey] ?? 'Series').trim() || 'Series'
    const point = {
      index: idx,
      x: String(row?.[xKey] ?? `Point ${idx + 1}`),
      y,
    }
    if (!grouped.has(groupName)) grouped.set(groupName, [])
    grouped.get(groupName).push(point)
  })

  return Array.from(grouped.entries()).map(([name, points]) => ({ name, points }))
}

function buildXDomain(groups) {
  const domain = []
  const seen = new Set()
  groups.forEach((group) => {
    const points = Array.isArray(group?.points) ? group.points : []
    points.forEach((point) => {
      const key = String(point?.x ?? '')
      if (!key || seen.has(key)) return
      seen.add(key)
      domain.push(key)
    })
  })
  return domain
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return 'n/a'
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Number(value))
}

function formatTime(value) {
  if (!value) return ''
  try {
    return new Date(value).toLocaleString()
  } catch {
    return ''
  }
}

function getStoredLayout() {
  if (typeof window === 'undefined') return 'comfort'
  const stored = window.localStorage.getItem(LAYOUT_KEY)
  if (stored === 'compact' || stored === 'comfort' || stored === 'focus') return stored
  return 'comfort'
}

function readSeriesPalette() {
  if (typeof window === 'undefined') return PALETTE
  const styles = window.getComputedStyle(document.documentElement)
  const values = []
  for (let idx = 1; idx <= 8; idx += 1) {
    const value = String(styles.getPropertyValue(`--viz-series-${idx}`) || '').trim()
    if (value) values.push(value)
  }
  return values.length ? values : PALETTE
}

function seriesColor(index, palette) {
  const active = Array.isArray(palette) && palette.length ? palette : PALETTE
  return active[index % active.length]
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max)
}

function buildScatterGroups(records, xKey, yKey, seriesKey, limit = 180) {
  const rows = Array.isArray(records) ? records.slice(0, limit) : []
  const hasSeries =
    seriesKey &&
    rows.some((row) => {
      const value = String(row?.[seriesKey] ?? '').trim()
      return Boolean(value)
    })

  const groupsMap = new Map()
  const setPoint = (name, point) => {
    if (!groupsMap.has(name)) groupsMap.set(name, [])
    groupsMap.get(name).push(point)
  }

  rows.forEach((row, idx) => {
    const y = toNumber(row?.[yKey])
    if (y === null) return
    const xRaw = toNumber(row?.[xKey])
    const x = xRaw === null ? idx + 1 : xRaw
    const groupName = hasSeries ? String(row?.[seriesKey] ?? 'Series').trim() || 'Series' : (yKey || 'value')
    setPoint(groupName, {
      x,
      y,
      label: String(row?.[xKey] ?? `Point ${idx + 1}`),
    })
  })

  return Array.from(groupsMap.entries()).map(([name, points]) => ({ name, points }))
}

function buildHistogramPoints(records, preferredKey = '', fallbackKey = '', bins = 10) {
  const rows = Array.isArray(records) ? records : []
  const first = rows[0] && typeof rows[0] === 'object' ? rows[0] : {}
  const keys = Object.keys(first)
  const keyCandidates = [preferredKey, fallbackKey, ...keys].filter(Boolean)
  let selectedKey = ''
  let values = []

  for (const key of keyCandidates) {
    const parsed = rows
      .map((row) => toNumber(row?.[key]))
      .filter((value) => value !== null)
    if (parsed.length >= 4) {
      selectedKey = key
      values = parsed
      break
    }
  }

  if (!values.length) return { key: '', points: [] }
  const min = Math.min(...values)
  const max = Math.max(...values)
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { key: '', points: [] }

  if (min === max) {
    return {
      key: selectedKey,
      points: [{ x: formatNumber(min), y: values.length, from: min, to: max }],
    }
  }

  const binCount = clamp(Number(bins) || 10, 4, 24)
  const binSize = (max - min) / binCount
  const counts = new Array(binCount).fill(0)

  values.forEach((value) => {
    const idx = clamp(Math.floor((value - min) / binSize), 0, binCount - 1)
    counts[idx] += 1
  })

  const points = counts.map((count, idx) => {
    const start = min + binSize * idx
    const end = min + binSize * (idx + 1)
    return {
      x: `${formatNumber(start)}–${formatNumber(end)}`,
      y: count,
      from: start,
      to: end,
    }
  })

  return { key: selectedKey, points }
}

function buildStackedBars(records, xKey, yKey, seriesKey, limit = 48) {
  if (!seriesKey) return { xDomain: [], seriesNames: [], rows: [] }
  const rows = Array.isArray(records) ? records.slice(0, limit) : []
  const matrix = new Map()
  const seriesSet = new Set()

  rows.forEach((row, idx) => {
    const y = toNumber(row?.[yKey])
    if (y === null) return
    const x = String(row?.[xKey] ?? `Point ${idx + 1}`)
    const series = String(row?.[seriesKey] ?? 'Series').trim() || 'Series'
    seriesSet.add(series)
    if (!matrix.has(x)) matrix.set(x, {})
    const cell = matrix.get(x)
    cell[series] = Number(cell[series] || 0) + y
  })

  const xDomain = Array.from(matrix.keys())
  const seriesNames = Array.from(seriesSet)
  const normalizedRows = xDomain.map((x) => {
    const totals = matrix.get(x) || {}
    const total = seriesNames.reduce((sum, name) => sum + Number(totals[name] || 0), 0)
    return { x, totals, total }
  })

  return { xDomain, seriesNames, rows: normalizedRows }
}

function buildPieSlices(groups) {
  if (!Array.isArray(groups) || !groups.length) return []

  if (groups.length > 1) {
    const rows = groups
      .map((group) => ({
        name: group.name,
        value: (group.points || []).reduce((sum, point) => sum + Number(point?.y || 0), 0),
      }))
      .filter((row) => row.value > 0)
    return rows.slice(0, 18)
  }

  const primary = groups[0]
  return (primary.points || [])
    .map((point) => ({
      name: String(point?.x ?? 'segment'),
      value: Number(point?.y || 0),
    }))
    .filter((row) => row.value > 0)
    .slice(0, 18)
}

function parsePointLiteral(value) {
  if (typeof value !== 'string') return null
  const text = value.trim()
  if (!text) return null
  const pointMatch = text.match(/POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)/i)
  if (pointMatch) {
    const lon = Number(pointMatch[1])
    const lat = Number(pointMatch[2])
    if (Number.isFinite(lat) && Number.isFinite(lon)) return { lat, lon }
  }
  const pairMatch = text.match(/(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)/)
  if (pairMatch) {
    const first = Number(pairMatch[1])
    const second = Number(pairMatch[2])
    if (Number.isFinite(first) && Number.isFinite(second)) {
      if (Math.abs(first) <= 90 && Math.abs(second) <= 180) return { lat: first, lon: second }
      if (Math.abs(second) <= 90 && Math.abs(first) <= 180) return { lat: second, lon: first }
    }
  }
  return null
}

function resolveGeoValue(row, key, type) {
  if (!row || typeof row !== 'object') return null
  const direct = key ? row[key] : undefined
  const directNum = toNumber(direct)
  if (directNum !== null) return directNum

  if (direct && typeof direct === 'object') {
    const nested = toNumber(
      type === 'lat'
        ? direct.lat ?? direct.latitude ?? direct.y
        : direct.lon ?? direct.lng ?? direct.longitude ?? direct.x
    )
    if (nested !== null) return nested
  }

  const fromDirectLiteral = parsePointLiteral(String(direct ?? ''))
  if (fromDirectLiteral) return type === 'lat' ? fromDirectLiteral.lat : fromDirectLiteral.lon

  const locationValue = row.location ?? row.the_geom ?? row.geometry
  const parsedLocation = parsePointLiteral(String(locationValue ?? ''))
  if (parsedLocation) return type === 'lat' ? parsedLocation.lat : parsedLocation.lon
  return null
}

function detectGeoKeys(records, artifact = {}) {
  const rows = Array.isArray(records) ? records : []
  const first = rows[0] && typeof rows[0] === 'object' ? rows[0] : {}
  const keys = Object.keys(first)
  const options = artifact && typeof artifact.chart_options === 'object' ? artifact.chart_options : {}
  const explicitLat = String(artifact?.lat_key || options?.lat_key || '').trim()
  const explicitLon = String(artifact?.lon_key || options?.lon_key || '').trim()

  const pickKey = (explicit, hints, type) => {
    if (explicit && keys.includes(explicit)) return explicit
    for (const key of keys) {
      const lowered = key.toLowerCase()
      if (hints.some((hint) => lowered.includes(hint))) return key
    }
    for (const key of keys) {
      if (rows.some((row) => resolveGeoValue(row, key, type) !== null)) return key
    }
    return ''
  }

  return {
    latKey: pickKey(explicitLat, LAT_KEY_HINTS, 'lat'),
    lonKey: pickKey(explicitLon, LON_KEY_HINTS, 'lon'),
  }
}

function buildMapSeries(records, artifact, fallbackLabelKey = '', fallbackSeriesKey = '') {
  const rows = Array.isArray(records) ? records.slice(0, 2500) : []
  const { latKey, lonKey } = detectGeoKeys(rows, artifact)
  const options = artifact && typeof artifact.chart_options === 'object' ? artifact.chart_options : {}
  const labelKey = String(artifact?.label_key || options?.label_key || fallbackLabelKey || '').trim()
  const weightKey = String(artifact?.weight_key || options?.weight_key || '').trim()
  const seriesKey = String(artifact?.series_key || options?.series_key || fallbackSeriesKey || '').trim()

  const groups = new Map()
  rows.forEach((row, idx) => {
    const lat = resolveGeoValue(row, latKey, 'lat')
    const lon = resolveGeoValue(row, lonKey, 'lon')
    if (lat === null || lon === null) return
    if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return

    const groupName = seriesKey ? String(row?.[seriesKey] ?? '').trim() || 'points' : 'points'
    if (!groups.has(groupName)) groups.set(groupName, [])
    groups.get(groupName).push({
      lat,
      lon,
      label: labelKey ? String(row?.[labelKey] ?? '').trim() : '',
      weight: toNumber(row?.[weightKey]) ?? 1,
      idx,
    })
  })

  const series = Array.from(groups.entries()).map(([name, points]) => ({ name, points }))
  const all = series.flatMap((entry) => entry.points)
  const bounds = all.length
    ? {
        minLat: Math.min(...all.map((point) => point.lat)),
        maxLat: Math.max(...all.map((point) => point.lat)),
        minLon: Math.min(...all.map((point) => point.lon)),
        maxLon: Math.max(...all.map((point) => point.lon)),
      }
    : null

  return { latKey, lonKey, labelKey, weightKey, series, bounds, total: all.length }
}

function createGeoProjector(bounds, viewport = { width: 100, height: 100, padding: 4 }) {
  const width = Number(viewport?.width || 100)
  const height = Number(viewport?.height || 100)
  const padding = Number(viewport?.padding || 0)
  const lonRange = Math.max((bounds?.maxLon ?? 0) - (bounds?.minLon ?? 0), 0.000001)
  const latRange = Math.max((bounds?.maxLat ?? 0) - (bounds?.minLat ?? 0), 0.000001)
  const usableWidth = Math.max(width - padding * 2, 1)
  const usableHeight = Math.max(height - padding * 2, 1)
  const scaleX = usableWidth / lonRange
  const scaleY = usableHeight / latRange
  const scale = Math.min(scaleX, scaleY)
  const drawWidth = lonRange * scale
  const drawHeight = latRange * scale
  const offsetX = padding + (usableWidth - drawWidth) / 2
  const offsetY = padding + (usableHeight - drawHeight) / 2

  return (point) => {
    const x = offsetX + (point.lon - (bounds?.minLon ?? 0)) * scale
    const y = offsetY + ((bounds?.maxLat ?? 0) - point.lat) * scale
    return { x: clamp(x, padding, width - padding), y: clamp(y, padding, height - padding) }
  }
}

function buildGeoHeatGrid(points, bounds, columns = 26, rows = 14) {
  if (!Array.isArray(points) || !points.length || !bounds) return { cells: [], max: 0 }
  const project = createGeoProjector(bounds, { width: columns, height: rows, padding: 0 })
  const matrix = Array.from({ length: rows }, () => Array.from({ length: columns }, () => 0))
  points.forEach((point) => {
    const projected = project(point)
    const col = clamp(Math.floor(projected.x), 0, columns - 1)
    const row = clamp(Math.floor(projected.y), 0, rows - 1)
    matrix[row][col] += Math.max(Number(point.weight) || 1, 1)
  })
  const flat = matrix.flat()
  const max = Math.max(...flat, 0)
  if (max <= 0) return { cells: [], max: 0 }

  const cells = []
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < columns; col += 1) {
      const value = matrix[row][col]
      if (value <= 0) continue
      cells.push({
        row,
        col,
        value,
      })
    }
  }
  return { cells, max, columns, rows }
}

function computeWeightStats(points) {
  if (!Array.isArray(points) || !points.length) return { min: 0, max: 0, median: 0 }
  const values = points
    .map((point) => Number(point?.weight || 0))
    .filter((value) => Number.isFinite(value) && value > 0)
    .sort((a, b) => a - b)
  if (!values.length) return { min: 0, max: 0, median: 0 }
  const mid = Math.floor(values.length / 2)
  const median = values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2
  return {
    min: values[0],
    max: values[values.length - 1],
    median,
  }
}

function pointRadius(weight, mode = 'points') {
  const base = mode === 'heatmap' ? 0.85 : 2.2
  return clamp(base + Math.log10(Math.max(Number(weight) || 1, 1)) * 1.2, mode === 'heatmap' ? 0.85 : 1.8, 9)
}

function mapCenter(bounds) {
  if (!bounds) return [40.73, -73.95]
  return [(bounds.minLat + bounds.maxLat) / 2, (bounds.minLon + bounds.maxLon) / 2]
}

function OSMFitBounds({ bounds, padding = [18, 18] }) {
  const map = useMap()
  useEffect(() => {
    if (!bounds) return
    const sw = [bounds.minLat, bounds.minLon]
    const ne = [bounds.maxLat, bounds.maxLon]
    map.fitBounds([sw, ne], { padding, maxZoom: 14 })
  }, [map, bounds, padding])
  return null
}

function OSMResizeSync() {
  const map = useMap()
  useEffect(() => {
    const container = map.getContainer()
    const trigger = () => map.invalidateSize(false)
    const timer = window.setTimeout(trigger, 80)
    window.addEventListener('resize', trigger)

    let observer = null
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => trigger())
      observer.observe(container)
      if (container.parentElement) observer.observe(container.parentElement)
    }

    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('resize', trigger)
      if (observer) observer.disconnect()
    }
  }, [map])
  return null
}

function ChartView({ artifact }) {
  const records = Array.isArray(artifact?.records) ? artifact.records : []
  const chartTypeRaw = String(artifact?.chart_type || 'table').toLowerCase().replace(/-/g, '_')
  const chartType =
    chartTypeRaw === 'timeseries'
      ? 'line'
      : chartTypeRaw === 'column'
        ? 'bar'
        : chartTypeRaw === 'kpi'
          ? 'metric'
          : chartTypeRaw === 'map_points' || chartTypeRaw === 'map_heatmap' || chartTypeRaw === 'geo' || chartTypeRaw === 'geospatial' || chartTypeRaw === 'heatmap'
            ? 'map'
            : chartTypeRaw
  const { xKey, yKey } = deriveKeys(records, artifact?.x_key, artifact?.y_key)
  const seriesKey = String(artifact?.series_key || '').trim()
  const palette = readSeriesPalette()

  const groups = useMemo(
    () => buildSeriesGroups(records, xKey, yKey, seriesKey, 36),
    [records, xKey, yKey, seriesKey]
  )

  const [hiddenSeries, setHiddenSeries] = useState([])
  const [hiddenSlices, setHiddenSlices] = useState([])
  const [tooltip, setTooltip] = useState(null)

  useEffect(() => {
    setHiddenSeries([])
    setHiddenSlices([])
    setTooltip(null)
  }, [artifact?.id, chartType, xKey, yKey, seriesKey])

  const activeGroups = groups.filter((group) => !hiddenSeries.includes(group.name))
  const xDomain = useMemo(() => buildXDomain(activeGroups.length ? activeGroups : groups), [activeGroups, groups])

  const maxY = Math.max(
    ...activeGroups.flatMap((group) =>
      (group.points || []).map((point) => Number(point?.y || 0))
    ),
    0
  )

  function toggleSeries(name) {
    setHiddenSeries((current) =>
      current.includes(name) ? current.filter((item) => item !== name) : [...current, name]
    )
  }

  function showTooltip(event, payload) {
    const host = event.currentTarget.closest('.vizInteractive')
    if (!host) return
    const hostRect = host.getBoundingClientRect()
    const pointRect = event.currentTarget.getBoundingClientRect()
    const pointX = pointRect.left - hostRect.left + pointRect.width / 2
    const pointY = pointRect.top - hostRect.top + pointRect.height / 2
    const minX = 84
    const maxX = Math.max(hostRect.width - 84, minX)
    const clampedX = Math.min(Math.max(pointX, minX), maxX)
    const preferAbove = pointY > 56
    const y = preferAbove ? pointY - 8 : pointY + 8
    setTooltip({
      x: clampedX,
      y,
      placement: preferAbove ? 'above' : 'below',
      ...payload,
    })
  }

  function clearTooltip() {
    setTooltip(null)
  }

  if (chartType === 'metric') {
    const firstValue =
      activeGroups[0]?.points?.[0]?.y ??
      groups[0]?.points?.[0]?.y ??
      toNumber(records[0]?.[yKey]) ??
      null
    return (
      <div className="vizMetric">
        <strong>{formatNumber(firstValue)}</strong>
        <span>{yKey || 'Metric value'}</span>
      </div>
    )
  }

  if (chartType === 'line' || chartType === 'area') {
    const hasRenderableSeries = activeGroups.some((group) => (group.points || []).length >= 2)
    if (!hasRenderableSeries || xDomain.length < 2) {
      return <p className="empty">No active series available. Toggle legend items to show data.</p>
    }

    return (
      <div className="vizInteractive" onMouseLeave={clearTooltip}>
        <div className="vizLegend">
          {groups.map((group, index) => {
            const hidden = hiddenSeries.includes(group.name)
            return (
              <button
                key={`legend-${group.name}`}
                type="button"
                className={`legendPill ${hidden ? 'off' : ''}`}
                onClick={() => toggleSeries(group.name)}
                title={hidden ? `Show ${group.name}` : `Hide ${group.name}`}
              >
                <span style={{ backgroundColor: seriesColor(index, palette) }} />
                {group.name}
              </button>
            )
          })}
        </div>

        <div className="vizLineWrap">
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="line-chart">
            {activeGroups.map((group, groupIndex) => {
              const points = (group.points || [])
                .map((point) => {
                  const xIdx = xDomain.indexOf(String(point?.x ?? ''))
                  if (xIdx < 0) return null
                  const x = (xIdx / Math.max(xDomain.length - 1, 1)) * 100
                  const y = maxY > 0 ? 100 - (Number(point?.y || 0) / maxY) * 100 : 100
                  return { ...point, x, y }
                })
                .filter(Boolean)

              if (points.length < 2) return null

              const linePath = points
                .map((point, idx) => `${idx === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
                .join(' ')
              const areaPath = `${linePath} L 100 100 L 0 100 Z`
              const color = seriesColor(groupIndex, palette)

              return (
                <g key={`line-${group.name}`}>
                  {chartType === 'area' ? (
                    <path d={areaPath} style={{ fill: color, opacity: 0.14 }} className="vizArea" />
                  ) : null}
                  <path
                    d={linePath}
                    style={{ stroke: color }}
                    className="vizPath"
                  />
                  {points.map((point) => (
                    <circle
                      key={`${group.name}-${point.x}-${point.y}`}
                      cx={point.x}
                      cy={point.y}
                      r="1.4"
                      style={{ fill: color }}
                      onMouseEnter={(event) =>
                        showTooltip(event, {
                          label: point.x,
                          value: point.y,
                          series: group.name,
                          color,
                        })
                      }
                    />
                  ))}
                </g>
              )
            })}
          </svg>
          <p className="vizAxis">
            <span>{xDomain[0] || xKey}</span>
            <span>{xDomain[xDomain.length - 1] || xKey}</span>
          </p>
        </div>

        {tooltip ? (
          <div
            className={`vizTooltip ${tooltip.placement === 'below' ? 'below' : 'above'}`}
            style={{ left: `${tooltip.x}px`, top: `${tooltip.y}px` }}
          >
            <strong>{tooltip.series}</strong>
            <span>{tooltip.label}</span>
            <span>{formatNumber(tooltip.value)}</span>
          </div>
        ) : null}
      </div>
    )
  }

  if (chartType === 'bar') {
    const primary = activeGroups[0] || groups[0]
    const points = Array.isArray(primary?.points) ? primary.points : []
    const localMaxY = Math.max(...points.map((point) => Number(point?.y || 0)), 0)

    if (!points.length) {
      return <p className="empty">No active series available. Toggle legend items to show data.</p>
    }

    return (
      <div className="vizInteractive" onMouseLeave={clearTooltip}>
        <div className="vizLegend">
          {groups.map((group, index) => {
            const hidden = hiddenSeries.includes(group.name)
            return (
              <button
                key={`legend-${group.name}`}
                type="button"
                className={`legendPill ${hidden ? 'off' : ''}`}
                onClick={() => toggleSeries(group.name)}
                title={hidden ? `Show ${group.name}` : `Hide ${group.name}`}
              >
                <span style={{ backgroundColor: seriesColor(index, palette) }} />
                {group.name}
              </button>
            )
          })}
        </div>

        <div className="vizBars" role="img" aria-label="bar-chart">
          {points.map((point, index) => {
            const heightPct = localMaxY > 0 ? Math.max((Number(point?.y || 0) / localMaxY) * 100, 4) : 4
            return (
              <div
                key={`${primary.name}-${point.x}-${index}`}
                className="vizBarColumn"
                onMouseEnter={(event) =>
                  showTooltip(event, {
                    label: point.x,
                    value: point.y,
                    series: primary.name,
                    color: seriesColor(0, palette),
                  })
                }
              >
                <div className="vizBar" style={{ height: `${heightPct}%` }} />
                <span>{point.x}</span>
              </div>
            )
          })}
        </div>

        {tooltip ? (
          <div
            className={`vizTooltip ${tooltip.placement === 'below' ? 'below' : 'above'}`}
            style={{ left: `${tooltip.x}px`, top: `${tooltip.y}px` }}
          >
            <strong>{tooltip.series}</strong>
            <span>{tooltip.label}</span>
            <span>{formatNumber(tooltip.value)}</span>
          </div>
        ) : null}
      </div>
    )
  }

  if (chartType === 'stacked_bar') {
    const stacked = buildStackedBars(records, xKey, yKey, seriesKey)
    const activeSeriesNames = stacked.seriesNames.filter((name) => !hiddenSeries.includes(name))
    const maxStackTotal = Math.max(
      ...stacked.rows.map((row) =>
        activeSeriesNames.reduce((sum, name) => sum + Number(row?.totals?.[name] || 0), 0)
      ),
      0
    )
    if (!stacked.rows.length || !activeSeriesNames.length) {
      return <p className="empty">Stacked bar needs x/y values with a series key and at least one active legend item.</p>
    }
    return (
      <div className="vizInteractive" onMouseLeave={clearTooltip}>
        <div className="vizLegend">
          {stacked.seriesNames.map((name, index) => {
            const hidden = hiddenSeries.includes(name)
            return (
              <button
                key={`legend-${name}`}
                type="button"
                className={`legendPill ${hidden ? 'off' : ''}`}
                onClick={() => toggleSeries(name)}
                title={hidden ? `Show ${name}` : `Hide ${name}`}
              >
                <span style={{ backgroundColor: seriesColor(index, palette) }} />
                {name}
              </button>
            )
          })}
        </div>
        <div className="vizStackedBars" role="img" aria-label="stacked-bar-chart">
          {stacked.rows.map((row, rowIdx) => {
            const activeTotal = activeSeriesNames.reduce(
              (sum, name) => sum + Number(row?.totals?.[name] || 0),
              0
            )
            return (
              <div key={`${row.x}-${rowIdx}`} className="vizStackedColumn">
                <div className="vizStackedTrack">
                  {activeSeriesNames.map((name, seriesIdx) => {
                    const value = Number(row?.totals?.[name] || 0)
                    if (!value) return null
                    const pct = maxStackTotal > 0 ? (value / maxStackTotal) * 100 : 0
                    return (
                      <button
                        key={`${row.x}-${name}`}
                        type="button"
                        className="vizStackedSegment"
                        style={{ height: `${Math.max(pct, 2)}%`, backgroundColor: seriesColor(seriesIdx, palette) }}
                        onMouseEnter={(event) =>
                          showTooltip(event, {
                            label: row.x,
                            value,
                            series: name,
                            color: seriesColor(seriesIdx, palette),
                          })
                        }
                        aria-label={`${name} at ${row.x}`}
                      />
                    )
                  })}
                </div>
                <span title={row.x}>{row.x}</span>
                <small>{formatNumber(activeTotal)}</small>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  if (chartType === 'histogram') {
    const histogram = buildHistogramPoints(records, yKey, xKey, artifact?.chart_options?.bins)
    const points = histogram.points
    const maxValue = Math.max(...points.map((point) => Number(point?.y || 0)), 0)
    if (!points.length) {
      return <p className="empty">Histogram needs at least one numeric field with enough values.</p>
    }
    return (
      <div className="vizInteractive" onMouseLeave={clearTooltip}>
        <div className="vizBars" role="img" aria-label="histogram-chart">
          {points.map((point, index) => {
            const heightPct = maxValue > 0 ? Math.max((Number(point?.y || 0) / maxValue) * 100, 4) : 4
            return (
              <div
                key={`hist-bin-${index}`}
                className="vizBarColumn"
                onMouseEnter={(event) =>
                  showTooltip(event, {
                    label: point.x,
                    value: point.y,
                    series: histogram.key || 'histogram',
                    color: seriesColor(0, palette),
                  })
                }
              >
                <div className="vizBar" style={{ height: `${heightPct}%` }} />
                <span title={point.x}>{point.x}</span>
              </div>
            )
          })}
        </div>
        {tooltip ? (
          <div
            className={`vizTooltip ${tooltip.placement === 'below' ? 'below' : 'above'}`}
            style={{ left: `${tooltip.x}px`, top: `${tooltip.y}px` }}
          >
            <strong>{tooltip.series}</strong>
            <span>{tooltip.label}</span>
            <span>{formatNumber(tooltip.value)}</span>
          </div>
        ) : null}
      </div>
    )
  }

  if (chartType === 'scatter') {
    const scatterGroups = buildScatterGroups(records, xKey, yKey, seriesKey, 220)
    const visibleGroups = scatterGroups.filter((group) => !hiddenSeries.includes(group.name))
    const renderGroups = visibleGroups.length ? visibleGroups : scatterGroups
    const points = renderGroups.flatMap((group) => group.points || [])
    if (points.length < 2) {
      return <p className="empty">Scatter plot needs numeric x/y data points.</p>
    }
    const minX = Math.min(...points.map((point) => Number(point?.x || 0)))
    const maxX = Math.max(...points.map((point) => Number(point?.x || 0)))
    const minY = Math.min(...points.map((point) => Number(point?.y || 0)))
    const maxY = Math.max(...points.map((point) => Number(point?.y || 0)))
    const xRange = maxX - minX || 1
    const yRange = maxY - minY || 1
    return (
      <div className="vizInteractive" onMouseLeave={clearTooltip}>
        <div className="vizLegend">
          {scatterGroups.map((group, index) => {
            const hidden = hiddenSeries.includes(group.name)
            return (
              <button
                key={`legend-${group.name}`}
                type="button"
                className={`legendPill ${hidden ? 'off' : ''}`}
                onClick={() => toggleSeries(group.name)}
                title={hidden ? `Show ${group.name}` : `Hide ${group.name}`}
              >
                <span style={{ backgroundColor: seriesColor(index, palette) }} />
                {group.name}
              </button>
            )
          })}
        </div>
        <div className="vizLineWrap">
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="scatter-plot">
            {renderGroups.map((group, groupIndex) => {
              const color = seriesColor(groupIndex, palette)
              return (group.points || []).map((point, idx) => {
                const x = ((Number(point?.x || 0) - minX) / xRange) * 100
                const y = 100 - ((Number(point?.y || 0) - minY) / yRange) * 100
                return (
                  <circle
                    key={`${group.name}-${idx}-${x}-${y}`}
                    cx={x}
                    cy={y}
                    r="1.5"
                    style={{ fill: color, opacity: 0.84 }}
                    onMouseEnter={(event) =>
                      showTooltip(event, {
                        label: point.label || `${formatNumber(point.x)}, ${formatNumber(point.y)}`,
                        value: point.y,
                        series: `${group.name} (${formatNumber(point.x)})`,
                        color,
                      })
                    }
                  />
                )
              })
            })}
          </svg>
          <p className="vizAxis">
            <span>{`${xKey || 'x'}: ${formatNumber(minX)}`}</span>
            <span>{`${xKey || 'x'}: ${formatNumber(maxX)}`}</span>
          </p>
        </div>
        {tooltip ? (
          <div
            className={`vizTooltip ${tooltip.placement === 'below' ? 'below' : 'above'}`}
            style={{ left: `${tooltip.x}px`, top: `${tooltip.y}px` }}
          >
            <strong>{tooltip.series}</strong>
            <span>{tooltip.label}</span>
            <span>{formatNumber(tooltip.value)}</span>
          </div>
        ) : null}
      </div>
    )
  }

  if (chartType === 'map') {
    const mapData = buildMapSeries(records, artifact, xKey, seriesKey)
    const options = artifact && typeof artifact.chart_options === 'object' ? artifact.chart_options : {}
    const mapModeRaw = String(options.map_mode || options.layer_type || 'points').trim().toLowerCase()
    const mapMode = mapModeRaw === 'heatmap' || mapModeRaw === 'density' ? 'heatmap' : 'points'
    const basemapRaw = String(options.basemap || options.map_provider || 'osm').trim().toLowerCase()
    const useOSM = !['none', 'off', 'false', 'svg'].includes(basemapRaw)

    const allSeries = mapData.series
    const visibleSeries = allSeries.filter((entry) => !hiddenSeries.includes(entry.name))
    const renderSeries = visibleSeries.length ? visibleSeries : allSeries
    const allPoints = renderSeries.flatMap((entry) => entry.points || [])

    if (!allPoints.length || !mapData.bounds) {
      return (
        <p className="empty">
          Map visualization needs valid latitude/longitude data (for example fields like latitude/longitude or POINT geometry).
        </p>
      )
    }

    const weightStats = computeWeightStats(allPoints)
    const heat = mapMode === 'heatmap' ? buildGeoHeatGrid(allPoints, mapData.bounds) : { cells: [], max: 0 }
    const project = createGeoProjector(mapData.bounds, { width: 100, height: 100, padding: 4 })
    const center = mapCenter(mapData.bounds)
    const mapKey = `${String(artifact?.id || 'map')}-${mapMode}-${Math.round(mapData.total / 10)}`

    return (
      <div className="vizInteractive" onMouseLeave={clearTooltip}>
        <div className="vizLegend">
          {allSeries.map((entry, idx) => {
            const hidden = hiddenSeries.includes(entry.name)
            return (
              <button
                key={`legend-${entry.name}`}
                type="button"
                className={`legendPill ${hidden ? 'off' : ''}`}
                onClick={() => toggleSeries(entry.name)}
                title={hidden ? `Show ${entry.name}` : `Hide ${entry.name}`}
              >
                <span style={{ backgroundColor: seriesColor(idx, palette) }} />
                {entry.name}
              </button>
            )
          })}
        </div>
        <div className="vizMapLegend">
          <span>
            mode: <strong>{useOSM ? `osm-${mapMode}` : `canvas-${mapMode}`}</strong>
          </span>
          <span>
            weights: min <strong>{formatNumber(weightStats.min)}</strong> · median{' '}
            <strong>{formatNumber(weightStats.median)}</strong> · max <strong>{formatNumber(weightStats.max)}</strong>
          </span>
        </div>
        <div className="vizMapWrap">
          {useOSM ? (
            <div className="vizLeafletShell">
              <MapContainer
                key={mapKey}
                center={center}
                zoom={11}
                className="vizLeafletMap"
                attributionControl={false}
                zoomControl
                scrollWheelZoom
              >
                <TileLayer
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                />
                <OSMResizeSync />
                <OSMFitBounds bounds={mapData.bounds} />
                {renderSeries.map((entry, groupIdx) =>
                  (entry.points || []).map((point, idx) => {
                    const color = seriesColor(groupIdx, palette)
                    return (
                      <CircleMarker
                        key={`${entry.name}-${idx}-${point.idx}`}
                        center={[point.lat, point.lon]}
                        radius={pointRadius(point.weight, mapMode)}
                        pathOptions={{
                          className: 'vizLeafletPoint',
                          color: '#ffffff',
                          weight: 1.6,
                          stroke: true,
                          fill: true,
                          fillColor: color,
                          fillOpacity: mapMode === 'heatmap' ? 0.42 : 0.9,
                          opacity: 1,
                        }}
                      >
                        <MapTooltip direction="top" offset={[0, -4]} opacity={0.95}>
                          <div className="mapTooltipContent">
                            <strong>{entry.name}</strong>
                            <div>{point.label || `${point.lat.toFixed(4)}, ${point.lon.toFixed(4)}`}</div>
                            <div>weight: {formatNumber(point.weight)}</div>
                          </div>
                        </MapTooltip>
                      </CircleMarker>
                    )
                  })
                )}
              </MapContainer>
            </div>
          ) : (
            <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" aria-label="map-visualization">
              <g className="vizMapGrid">
                <path d="M 4 50 L 96 50" />
                <path d="M 50 4 L 50 96" />
                <path d="M 4 27 L 96 27" />
                <path d="M 4 73 L 96 73" />
                <path d="M 27 4 L 27 96" />
                <path d="M 73 4 L 73 96" />
              </g>

              {mapMode === 'heatmap' && heat.max > 0
                ? heat.cells.map((cell, idx) => {
                    const x = (cell.col / heat.columns) * 100
                    const y = (cell.row / heat.rows) * 100
                    const width = 100 / heat.columns
                    const height = 100 / heat.rows
                    const intensity = clamp(cell.value / heat.max, 0.12, 1)
                    return (
                      <rect
                        key={`heat-${idx}`}
                        x={x}
                        y={y}
                        width={width}
                        height={height}
                        style={{ fill: 'var(--brand)', opacity: intensity * 0.65 }}
                      />
                    )
                  })
                : null}

              {renderSeries.map((entry, groupIdx) =>
                (entry.points || []).map((point, idx) => {
                  const projected = project(point)
                  const color = seriesColor(groupIdx, palette)
                  return (
                    <circle
                      key={`${entry.name}-${idx}-${point.idx}`}
                      cx={projected.x}
                      cy={projected.y}
                      r={pointRadius(point.weight, mapMode)}
                      style={{ fill: color, opacity: mapMode === 'heatmap' ? 0.24 : 0.82 }}
                      onMouseEnter={(event) =>
                        showTooltip(event, {
                          label: point.label || `${point.lat.toFixed(4)}, ${point.lon.toFixed(4)}`,
                          value: point.weight,
                          series: entry.name,
                          color,
                        })
                      }
                    />
                  )
                })
              )}
            </svg>
          )}
          <p className="vizAxis">
            <span>
              SW {mapData.bounds.minLat.toFixed(3)}, {mapData.bounds.minLon.toFixed(3)}
            </span>
            <span>
              NE {mapData.bounds.maxLat.toFixed(3)}, {mapData.bounds.maxLon.toFixed(3)}
            </span>
          </p>
        </div>
        {tooltip ? (
          <div
            className={`vizTooltip ${tooltip.placement === 'below' ? 'below' : 'above'}`}
            style={{ left: `${tooltip.x}px`, top: `${tooltip.y}px` }}
          >
            <strong>{tooltip.series}</strong>
            <span>{tooltip.label}</span>
            <span>{formatNumber(tooltip.value)}</span>
          </div>
        ) : null}
      </div>
    )
  }

  if (chartType === 'pie' || chartType === 'donut') {
    const slices = buildPieSlices(activeGroups.length ? activeGroups : groups)
      .filter((slice) => !hiddenSlices.includes(slice.name))
    const fullSlices = buildPieSlices(groups)
    const total = slices.reduce((sum, slice) => sum + Number(slice?.value || 0), 0)
    if (!slices.length || total <= 0) {
      return <p className="empty">Pie/Donut chart needs positive category values.</p>
    }

    let cumulative = 0
    const radius = 42
    const innerRadius = chartType === 'donut' ? 22 : 0

    const arcPath = (start, end) => {
      const startAngle = (start - 0.25) * Math.PI * 2
      const endAngle = (end - 0.25) * Math.PI * 2
      const x1 = 50 + radius * Math.cos(startAngle)
      const y1 = 50 + radius * Math.sin(startAngle)
      const x2 = 50 + radius * Math.cos(endAngle)
      const y2 = 50 + radius * Math.sin(endAngle)
      const largeArc = end - start > 0.5 ? 1 : 0

      if (!innerRadius) {
        return `M 50 50 L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z`
      }

      const ix1 = 50 + innerRadius * Math.cos(endAngle)
      const iy1 = 50 + innerRadius * Math.sin(endAngle)
      const ix2 = 50 + innerRadius * Math.cos(startAngle)
      const iy2 = 50 + innerRadius * Math.sin(startAngle)
      return [
        `M ${x1} ${y1}`,
        `A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2}`,
        `L ${ix1} ${iy1}`,
        `A ${innerRadius} ${innerRadius} 0 ${largeArc} 0 ${ix2} ${iy2}`,
        'Z',
      ].join(' ')
    }

    return (
      <div className="vizInteractive" onMouseLeave={clearTooltip}>
        <div className="vizLegend">
          {fullSlices.map((slice, idx) => {
            const hidden = hiddenSlices.includes(slice.name)
            return (
              <button
                key={`legend-${slice.name}`}
                type="button"
                className={`legendPill ${hidden ? 'off' : ''}`}
                onClick={() =>
                  setHiddenSlices((current) =>
                    current.includes(slice.name)
                      ? current.filter((item) => item !== slice.name)
                      : [...current, slice.name]
                  )
                }
              >
                <span style={{ backgroundColor: seriesColor(idx, palette) }} />
                {slice.name}
              </button>
            )
          })}
        </div>

        <div className="vizPieWrap">
          <svg viewBox="0 0 100 100" aria-label={`${chartType}-chart`}>
            {slices.map((slice, idx) => {
              const start = cumulative
              const fraction = Number(slice?.value || 0) / total
              cumulative += fraction
              const end = cumulative
              const path = arcPath(start, end)
              return (
                <path
                  key={`slice-${slice.name}-${idx}`}
                  d={path}
                  style={{ fill: seriesColor(idx, palette) }}
                  onMouseEnter={(event) =>
                    showTooltip(event, {
                      label: slice.name,
                      value: `${formatNumber(slice.value)} (${Math.round(fraction * 100)}%)`,
                      series: 'share',
                      color: seriesColor(idx, palette),
                    })
                  }
                />
              )
            })}
          </svg>
        </div>

        {tooltip ? (
          <div
            className={`vizTooltip ${tooltip.placement === 'below' ? 'below' : 'above'}`}
            style={{ left: `${tooltip.x}px`, top: `${tooltip.y}px` }}
          >
            <strong>{tooltip.series}</strong>
            <span>{tooltip.label}</span>
            <span>{String(tooltip.value)}</span>
          </div>
        ) : null}
      </div>
    )
  }

  const previewRows = records.slice(0, 8)
  const columns = Object.keys(previewRows[0] || {}).slice(0, 8)
  if (!columns.length) {
    return <p className="empty">No structured records available for chart rendering.</p>
  }
  return (
    <div className="vizTableWrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {previewRows.map((row, rowIndex) => (
            <tr key={`row-${rowIndex}`}>
              {columns.map((column) => (
                <td key={`${rowIndex}-${column}`}>{String(row?.[column] ?? '')}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function VisualizationCard({ artifact }) {
  const insights = Array.isArray(artifact?.insights) ? artifact.insights.filter(Boolean) : []
  const chartType = String(artifact?.chart_type || 'table')
  const recordCount = Array.isArray(artifact?.records) ? artifact.records.length : 0
  const [expanded, setExpanded] = useState(false)

  const summary = String(artifact?.summary || '').trim() || 'Generated by the agent based on MCP output.'
  const visibleInsights = expanded ? insights : insights.slice(0, 3)

  return (
    <article className={`vizCard ${expanded ? 'expanded' : ''}`}>
      <header className="vizCardHead">
        <div>
          <h3>{String(artifact?.title || 'Untitled visualization')}</h3>
          <p className={`vizSummary ${expanded ? 'expanded' : ''}`}>{summary}</p>
        </div>
        <div className="vizMeta">
          <span className="badge enabled">{chartType}</span>
          <span className="badge">{recordCount} rows</span>
          <button type="button" className="btn ghost vizExpandBtn" onClick={() => setExpanded((v) => !v)}>
            {expanded ? 'Collapse' : 'Expand'}
          </button>
        </div>
      </header>

      <div className="vizPanel">
        <ChartView artifact={artifact} />
      </div>

      {insights.length ? (
        <ul className="vizInsights">
          {visibleInsights.map((item, idx) => (
            <li key={`insight-${idx}`}>{String(item)}</li>
          ))}
        </ul>
      ) : null}

      <footer className="vizCardFoot">
        <span>{String(artifact?.source || '').trim() || 'source: agent-runtime'}</span>
        <span>{formatTime(artifact?.created_at)}</span>
      </footer>
    </article>
  )
}

export default function VisualizationCanvas({ visualizations = [], onClear, onOpenChat }) {
  const rows = Array.isArray(visualizations) ? visualizations : []
  const [layoutMode, setLayoutMode] = useState(() => getStoredLayout())

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(LAYOUT_KEY, layoutMode)
  }, [layoutMode])

  return (
    <section className="canvasSection">
      <div className="canvasHead">
        <div>
          <p className="overviewEyebrow">Agent Output</p>
          <h2>Visualization Canvas</h2>
          <p className="settingsHint">
            The agent can publish chart artifacts here during analysis. Ask for a trend chart, KPI panel,
            or comparative visualization.
          </p>
        </div>
        <div className="canvasActions">
          <div className="layoutModeGroup" role="group" aria-label="canvas-layout-mode">
            <button
              type="button"
              className={`btn ghost ${layoutMode === 'comfort' ? 'active' : ''}`}
              onClick={() => setLayoutMode('comfort')}
            >
              Comfort
            </button>
            <button
              type="button"
              className={`btn ghost ${layoutMode === 'compact' ? 'active' : ''}`}
              onClick={() => setLayoutMode('compact')}
            >
              Compact
            </button>
            <button
              type="button"
              className={`btn ghost ${layoutMode === 'focus' ? 'active' : ''}`}
              onClick={() => setLayoutMode('focus')}
            >
              Focus
            </button>
          </div>

          <div className="formActions">
            <button type="button" className="btn ghost" onClick={onOpenChat}>
              Prompt agent
            </button>
            <button type="button" className="btn ghost" onClick={onClear} disabled={!rows.length}>
              Clear canvas
            </button>
          </div>
        </div>
      </div>

      {!rows.length ? (
        <div className="canvasEmpty">
          <p>No visualizations yet.</p>
          <ul>
            <li>Try: "Query NYC 311 by month and create a line visualization."</li>
            <li>Try: "Summarize top complaint categories and publish a bar chart."</li>
            <li>Try: "Create a metric card for unresolved HEAT/HOT WATER tickets."</li>
            <li>Try: "Map noise complaints with a heatmap by latitude/longitude."</li>
          </ul>
        </div>
      ) : (
        <div className={`canvasGrid layout-${layoutMode}`}>
          {rows.map((artifact, index) => (
            <VisualizationCard
              key={String(artifact?.id || `artifact-${index}`)}
              artifact={artifact}
            />
          ))}
        </div>
      )}
    </section>
  )
}
