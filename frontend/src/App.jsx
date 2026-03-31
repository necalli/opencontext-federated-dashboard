import { useEffect, useMemo, useState } from 'react'
import ServerRegistry from './components/mcp/ServerRegistry'
import ToolExplorer from './components/mcp/ToolExplorer'
import RunTimeline from './components/mcp/RunTimeline'
import VisualizationCanvas from './components/mcp/VisualizationCanvas'
import AgentChat from './components/AgentChat'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5100'
const THEME_KEY = 'opencontext-theme'
const THEME_PALETTE_KEY = 'opencontext-theme-palette'
const THEME_SURFACE_KEY = 'opencontext-theme-surface'
const RAIL_KEY = 'opencontext-rail-collapsed'
const VISUALIZATION_KEY = 'opencontext-visualizations'

const NAV_ITEMS = [
  { id: 'overview', label: 'Overview' },
  { id: 'canvas', label: 'Visualization Canvas' },
  { id: 'servers', label: 'Server Registry' },
  { id: 'tools', label: 'Tool Explorer' },
  { id: 'runs', label: 'Run Timeline' },
  { id: 'settings', label: 'Settings' }
]

const quickPrompts = [
  'Find 5 transportation safety datasets and summarize availability.',
  'Inspect schema for a Boston 311 resource and suggest KPI fields.',
  'Run a constrained SQL trend query and explain actionable findings.'
]

const PALETTE_OPTIONS = [
  { value: 'ocean', label: 'Ocean (Blue)' },
  { value: 'onyx', label: 'Onyx (Black)' },
  { value: 'forest', label: 'Forest (Green)' },
  { value: 'ember', label: 'Ember (Red)' },
  { value: 'amber', label: 'Amber (Orange)' },
  { value: 'cream', label: 'Cream (Beige)' }
]

const SURFACE_OPTIONS = [
  { value: 'gradient', label: 'Gradient' },
  { value: 'matte', label: 'Matte' }
]

function isPalette(value) {
  return PALETTE_OPTIONS.some((entry) => entry.value === value)
}

function isSurface(value) {
  return SURFACE_OPTIONS.some((entry) => entry.value === value)
}

function getStoredTheme() {
  if (typeof window === 'undefined') return 'dark'
  const stored = window.localStorage.getItem(THEME_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  return 'dark'
}

function getStoredPalette() {
  if (typeof window === 'undefined') return 'ocean'
  const stored = String(window.localStorage.getItem(THEME_PALETTE_KEY) || '').trim().toLowerCase()
  return isPalette(stored) ? stored : 'ocean'
}

function getStoredSurface() {
  if (typeof window === 'undefined') return 'gradient'
  const stored = String(window.localStorage.getItem(THEME_SURFACE_KEY) || '').trim().toLowerCase()
  return isSurface(stored) ? stored : 'gradient'
}

function getStoredRailState() {
  if (typeof window === 'undefined') return false
  return window.localStorage.getItem(RAIL_KEY) === 'true'
}

function getStoredVisualizations() {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(VISUALIZATION_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item) => item && typeof item === 'object')
  } catch {
    return []
  }
}

async function readJson(url) {
  const response = await fetch(url)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const message =
      payload?.error?.message || payload?.message || `Request failed (${response.status})`
    throw new Error(String(message))
  }
  return payload
}

function formatTime(value) {
  if (!value) return '-'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return String(value)
  }
}

function OverviewPanel({
  summary,
  loading,
  error,
  onRefresh,
  onSelectView,
  onOpenChat,
  visualizationCount = 0
}) {
  const enabledServers = Number(summary?.enabledServers || 0)
  const totalServers = Number(summary?.totalServers || 0)
  const toolsCount = Number(summary?.toolsCount || 0)
  const runCount = Number(summary?.runCount || 0)
  const runtimeMode = String(summary?.runtimeMode || 'agent_sdk')
  const health = String(summary?.health || 'unknown')
  const latestRuns = Array.isArray(summary?.latestRuns) ? summary.latestRuns : []

  return (
    <section className="overviewPanel">
      <div className="overviewHeader">
        <div>
          <p className="overviewEyebrow">OpenContext Control Plane</p>
          <h2>Command center for federated MCP operations</h2>
          <p>
            Start from server connectivity, then move into tool execution and trace-based validation.
            Agent chat is available anytime from the floating button.
          </p>
        </div>
        <button type="button" className="btn ghost" onClick={onRefresh} disabled={loading}>
          {loading ? 'Refreshing...' : 'Refresh snapshot'}
        </button>
      </div>

      {error ? <p className="status error">{error}</p> : null}

      <div className="overviewStats">
        <article className="statCard">
          <p>Health</p>
          <strong className={health === 'ok' ? 'value good' : 'value warn'}>{health}</strong>
        </article>
        <article className="statCard">
          <p>Servers</p>
          <strong className="value">
            {enabledServers} / {totalServers} enabled
          </strong>
        </article>
        <article className="statCard">
          <p>Catalog tools</p>
          <strong className="value">{toolsCount}</strong>
        </article>
        <article className="statCard">
          <p>Visualizations</p>
          <strong className="value">{Number(visualizationCount || 0)}</strong>
        </article>
        <article className="statCard">
          <p>Recent runs</p>
          <strong className="value">{runCount}</strong>
        </article>
        <article className="statCard">
          <p>Primary runtime</p>
          <strong className="value">{runtimeMode}</strong>
        </article>
      </div>

      <div className="overviewGrid">
        <article className="overviewCard">
          <h3>Quick start</h3>
          <ol>
            <li>Add or verify at least one MCP server in Registry.</li>
            <li>Use Tool Explorer to validate routing and tool arguments.</li>
            <li>Use Run Timeline to inspect event-level execution traces.</li>
          </ol>
          <div className="quickActions">
            <button type="button" className="btn" onClick={() => onSelectView('servers')}>
              Go to Registry
            </button>
            <button type="button" className="btn ghost" onClick={() => onSelectView('tools')}>
              Open Tool Explorer
            </button>
            <button type="button" className="btn ghost" onClick={() => onSelectView('runs')}>
              Review Runs
            </button>
          </div>
        </article>

        <article className="overviewCard">
          <h3>Suggested first prompts</h3>
          <ul className="promptList">
            {quickPrompts.map((prompt) => (
              <li key={prompt}>
                <code>{prompt}</code>
              </li>
            ))}
          </ul>
          <button type="button" className="btn" onClick={onOpenChat}>
            Open AI Chat
          </button>
        </article>

        <article className="overviewCard">
          <h3>Latest activity</h3>
          {latestRuns.length === 0 ? (
            <p className="empty">No run traces yet. Start with a tool call or chat turn.</p>
          ) : (
            <ul className="latestRunList">
              {latestRuns.slice(0, 5).map((item) => (
                <li key={item.run_id}>
                  <div>
                    <strong>{item.runtime || 'runtime'}</strong>
                    <p>{item.message_preview || '(no message preview)'}</p>
                  </div>
                  <small>
                    {formatTime(item.created_at)} - {item.duration_ms || 0} ms
                  </small>
                </li>
              ))}
            </ul>
          )}
        </article>
      </div>
    </section>
  )
}

export default function App() {
  const [theme, setTheme] = useState(() => getStoredTheme())
  const [themePalette, setThemePalette] = useState(() => getStoredPalette())
  const [themeSurface, setThemeSurface] = useState(() => getStoredSurface())
  const [railCollapsed, setRailCollapsed] = useState(() => getStoredRailState())
  const [activeView, setActiveView] = useState('overview')
  const [chatLaunchSignal, setChatLaunchSignal] = useState(0)
  const [visualizations, setVisualizations] = useState(() => getStoredVisualizations())
  const [summary, setSummary] = useState({
    health: 'unknown',
    runtimeMode: 'agent_sdk',
    totalServers: 0,
    enabledServers: 0,
    toolsCount: 0,
    runCount: 0,
    latestRuns: []
  })
  const [snapshotLoading, setSnapshotLoading] = useState(false)
  const [snapshotError, setSnapshotError] = useState('')

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    window.localStorage.setItem(THEME_KEY, theme)
  }, [theme])

  useEffect(() => {
    document.documentElement.setAttribute('data-palette', themePalette)
    window.localStorage.setItem(THEME_PALETTE_KEY, themePalette)
  }, [themePalette])

  useEffect(() => {
    document.documentElement.setAttribute('data-surface', themeSurface)
    window.localStorage.setItem(THEME_SURFACE_KEY, themeSurface)
  }, [themeSurface])

  useEffect(() => {
    window.localStorage.setItem(RAIL_KEY, railCollapsed ? 'true' : 'false')
  }, [railCollapsed])

  useEffect(() => {
    try {
      window.localStorage.setItem(VISUALIZATION_KEY, JSON.stringify(visualizations))
    } catch {
      return
    }
  }, [visualizations])

  function handleVisualizationAdded(artifact) {
    if (!artifact || typeof artifact !== 'object') return
    const id = String(artifact.id || '').trim()
    if (!id) return
    setVisualizations((current) => {
      const filtered = current.filter((item) => String(item?.id || '').trim() !== id)
      return [artifact, ...filtered].slice(0, 50)
    })
  }

  function clearVisualizations() {
    setVisualizations([])
  }

  async function refreshSnapshot() {
    setSnapshotLoading(true)
    setSnapshotError('')
    const nextSummary = {
      health: 'unknown',
      runtimeMode: 'agent_sdk',
      totalServers: 0,
      enabledServers: 0,
      toolsCount: 0,
      runCount: 0,
      latestRuns: []
    }

    try {
      const [healthResult, runtimeResult, serversResult, toolsResult, runsResult] = await Promise.allSettled([
        readJson(`${API_BASE}/health`),
        readJson(`${API_BASE}/api/v1/system/info`),
        readJson(`${API_BASE}/api/v1/mcp/servers`),
        readJson(`${API_BASE}/api/v1/mcp/tools/list`),
        readJson(`${API_BASE}/api/v1/runs?limit=10`)
      ])

      if (healthResult.status === 'fulfilled') {
        nextSummary.health = String(healthResult.value?.status || 'unknown')
      }
      if (runtimeResult.status === 'fulfilled') {
        nextSummary.runtimeMode = String(runtimeResult.value?.runtime_mode || 'agent_sdk')
      }
      if (serversResult.status === 'fulfilled') {
        const rows = Array.isArray(serversResult.value?.servers) ? serversResult.value.servers : []
        nextSummary.totalServers = rows.length
        nextSummary.enabledServers = rows.filter((row) => row?.enabled !== false).length
      }
      if (toolsResult.status === 'fulfilled') {
        nextSummary.toolsCount = Number(toolsResult.value?.tool_count || 0)
      }
      if (runsResult.status === 'fulfilled') {
        const rows = Array.isArray(runsResult.value?.runs) ? runsResult.value.runs : []
        nextSummary.runCount = rows.length
        nextSummary.latestRuns = rows
      }

      setSummary(nextSummary)

      const failures = [healthResult, runtimeResult, serversResult, toolsResult, runsResult].filter(
        (item) => item.status === 'rejected'
      )
      if (failures.length > 0) {
        setSnapshotError('Some overview data could not be loaded. Core features are still available.')
      }
    } catch (err) {
      setSnapshotError(err?.message || 'Failed to load overview snapshot.')
    } finally {
      setSnapshotLoading(false)
    }
  }

  useEffect(() => {
    refreshSnapshot()
  }, [])

  const activeLabel = useMemo(() => {
    const item = NAV_ITEMS.find((entry) => entry.id === activeView)
    return item?.label || 'Overview'
  }, [activeView])

  function renderMainView() {
    if (activeView === 'servers') return <ServerRegistry />
    if (activeView === 'canvas') {
      return (
        <VisualizationCanvas
          visualizations={visualizations}
          onClear={clearVisualizations}
          onOpenChat={() => setChatLaunchSignal((v) => v + 1)}
        />
      )
    }
    if (activeView === 'tools') return <ToolExplorer />
    if (activeView === 'runs') return <RunTimeline />
    if (activeView === 'settings') {
      return (
        <section className="settingsPanel">
          <h2>General Settings</h2>
          <p className="settingsHint">
            Configure presentation and workspace behavior. Runtime and MCP controls stay in their dedicated modules.
          </p>
          <div className="settingsCard">
            <label className="settingRow">
              <span>Color mode</span>
              <button
                type="button"
                className="btn ghost"
                onClick={() => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))}
              >
                Switch to {theme === 'dark' ? 'light' : 'dark'} mode
              </button>
            </label>
            <label className="settingRow">
              <span>Color palette</span>
              <select
                value={themePalette}
                onChange={(event) => setThemePalette(event.target.value)}
                aria-label="theme-palette"
              >
                {PALETTE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="settingRow">
              <span>Surface style</span>
              <select
                value={themeSurface}
                onChange={(event) => setThemeSurface(event.target.value)}
                aria-label="theme-surface"
              >
                {SURFACE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="settingRow">
              <span>Left rail</span>
              <button
                type="button"
                className="btn ghost"
                onClick={() => setRailCollapsed((current) => !current)}
              >
                {railCollapsed ? 'Expand rail' : 'Collapse rail'}
              </button>
            </label>
            <label className="settingRow">
              <span>AI Chat Drawer</span>
              <button type="button" className="btn ghost" onClick={() => setChatLaunchSignal((v) => v + 1)}>
                Open chat now
              </button>
            </label>
          </div>
        </section>
      )
    }
    return (
      <OverviewPanel
        summary={summary}
        loading={snapshotLoading}
        error={snapshotError}
        onRefresh={refreshSnapshot}
        onSelectView={setActiveView}
        onOpenChat={() => setChatLaunchSignal((v) => v + 1)}
        visualizationCount={visualizations.length}
      />
    )
  }

  return (
    <div className={`appShell ${railCollapsed ? 'railCollapsed' : ''}`}>
      <aside className="leftRail" aria-label="control-rail">
        <div className="railTop">
          <button
            type="button"
            className="railCollapseBtn"
            onClick={() => setRailCollapsed((current) => !current)}
            title={railCollapsed ? 'Expand navigation rail' : 'Collapse navigation rail'}
          >
            {railCollapsed ? '>>' : '<<'}
          </button>
          <div className="brandBlock">
            <p className="brandEyebrow">OpenContext</p>
            <h1>Federated Dashboard</h1>
          </div>
        </div>

        <nav className="railNav">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`railNavBtn ${activeView === item.id ? 'active' : ''}`}
              onClick={() => setActiveView(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="railFooter">
          <button
            type="button"
            className="btn ghost"
            onClick={() => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))}
          >
            Theme: {theme === 'dark' ? 'Dark' : 'Light'} ·{' '}
            {(PALETTE_OPTIONS.find((row) => row.value === themePalette)?.label || themePalette).split(' ')[0]}
          </button>
          <button type="button" className="btn" onClick={() => setChatLaunchSignal((v) => v + 1)}>
            Open AI Chat
          </button>
        </div>
      </aside>

      <div className="workspace">
        <header className="workspaceHeader">
          <div>
            <p className="workspaceKicker">Workspace</p>
            <h2>{activeLabel}</h2>
          </div>
          <div className="workspaceActions">
            <button type="button" className="btn ghost" onClick={refreshSnapshot} disabled={snapshotLoading}>
              {snapshotLoading ? 'Refreshing...' : 'Sync snapshot'}
            </button>
          </div>
        </header>

        <main className="workspaceMain">{renderMainView()}</main>
      </div>

      <AgentChat launchSignal={chatLaunchSignal} onVisualization={handleVisualizationAdded} />
    </div>
  )
}
