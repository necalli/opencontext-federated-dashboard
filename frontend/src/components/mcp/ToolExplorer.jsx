import { useEffect, useMemo, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5100'

function extractError(payload, fallback) {
  if (payload?.error?.message) return payload.error.message
  if (typeof payload?.message === 'string' && payload.message) return payload.message
  return fallback
}

function prettyJson(value) {
  return JSON.stringify(value, null, 2)
}

export default function ToolExplorer() {
  const [servers, setServers] = useState([])
  const [selectedServerId, setSelectedServerId] = useState('')
  const [tools, setTools] = useState([])
  const [catalogErrors, setCatalogErrors] = useState([])
  const [loadingServers, setLoadingServers] = useState(false)
  const [loadingTools, setLoadingTools] = useState(false)
  const [running, setRunning] = useState(false)
  const [search, setSearch] = useState('')
  const [toolName, setToolName] = useState('')
  const [argsText, setArgsText] = useState('{\n  "query": "public safety",\n  "limit": 5\n}')
  const [resultText, setResultText] = useState('')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [advancedSqlMode, setAdvancedSqlMode] = useState(false)

  const isExecuteSqlTool = useMemo(
    () => String(toolName || '').trim().toLowerCase().endsWith('execute_sql'),
    [toolName]
  )
  const sqlPreview = useMemo(() => {
    if (!isExecuteSqlTool) return { query: '', parseError: '' }
    try {
      const parsed = JSON.parse(argsText || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { query: '', parseError: 'Arguments must be a JSON object for SQL preview.' }
      }
      const raw = String(parsed.query || parsed.sql || '').trim()
      return { query: raw, parseError: '' }
    } catch (err) {
      return { query: '', parseError: err?.message || 'Invalid JSON' }
    }
  }, [argsText, isExecuteSqlTool])

  const runServers = useMemo(() => {
    const enabled = servers.filter((server) => Boolean(server?.enabled))
    if (enabled.length > 0) return enabled

    const inferred = []
    const seen = new Set()
    for (const tool of tools) {
      const id = String(tool?.server_id || '').trim()
      const name = String(tool?.server_name || '').trim()
      if (!id || seen.has(id)) continue
      seen.add(id)
      inferred.push({ id, name: name || id, enabled: true })
    }
    return inferred
  }, [servers, tools])

  async function loadServers() {
    setLoadingServers(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/api/v1/mcp/servers`)
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(extractError(payload, `Load servers failed (${res.status})`))
      const rows = Array.isArray(payload?.servers) ? payload.servers : []
      setServers(rows)
      if (!selectedServerId) {
        const enabledFirst = rows.find((row) => Boolean(row?.enabled))
        if (enabledFirst?.id) setSelectedServerId(String(enabledFirst.id))
      }
    } catch (err) {
      setError(err?.message || 'Failed to load servers.')
    } finally {
      setLoadingServers(false)
    }
  }

  async function loadTools() {
    setLoadingTools(true)
    setError('')
    setInfo('')
    setCatalogErrors([])
    try {
      const query = selectedServerId ? `?server_id=${encodeURIComponent(selectedServerId)}` : ''
      const res = await fetch(`${API_BASE}/api/v1/mcp/tools/list${query}`)
      const payload = await res.json().catch(() => ({}))
      const rows = Array.isArray(payload?.tools) ? payload.tools : []
      const serverErrors = Array.isArray(payload?.errors) ? payload.errors : []
      const catalogServers = Array.isArray(payload?.servers) ? payload.servers : []
      setTools(rows)
      setCatalogErrors(serverErrors)

      if (catalogServers.length > 0) {
        setServers((current) => {
          const byId = new Map()
          for (const row of current) {
            const id = String(row?.id || '').trim()
            if (id) byId.set(id, row)
          }
          for (const row of catalogServers) {
            const id = String(row?.id || '').trim()
            if (!id) continue
            byId.set(id, {
              id,
              name: String(row?.name || id),
              endpoint: String(row?.endpoint || ''),
              enabled: row?.enabled !== false
            })
          }
          return [...byId.values()]
        })
      }

      if (!selectedServerId) {
        const preferredServer = catalogServers.find((row) => row?.enabled !== false) || catalogServers[0]
        const preferredId = String(preferredServer?.id || '').trim()
        if (preferredId) setSelectedServerId(preferredId)
      }

      if (!toolName && rows.length > 0) {
        setToolName(String(rows[0].name || ''))
      }

      if (!res.ok && rows.length === 0) {
        throw new Error(extractError(payload, `Load tools failed (${res.status})`))
      }

      if (serverErrors.length > 0) {
        setInfo(`Loaded ${rows.length} tools with ${serverErrors.length} server error(s).`)
      } else {
        setInfo(`Loaded ${rows.length} tools.`)
      }
    } catch (err) {
      setTools([])
      setError(err?.message || 'Failed to load tool catalog.')
    } finally {
      setLoadingTools(false)
    }
  }

  useEffect(() => {
    loadServers()
  }, [])

  useEffect(() => {
    loadTools()
  }, [selectedServerId])

  const filteredTools = useMemo(() => {
    const needle = search.trim().toLowerCase()
    if (!needle) return tools
    return tools.filter((tool) => {
      const name = String(tool?.name || '').toLowerCase()
      const desc = String(tool?.description || '').toLowerCase()
      const server = String(tool?.server_name || '').toLowerCase()
      return name.includes(needle) || desc.includes(needle) || server.includes(needle)
    })
  }, [tools, search])

  function selectTool(tool) {
    const nextName = String(tool?.name || '').trim()
    const nextServer = String(tool?.server_id || '').trim()
    if (nextName) setToolName(nextName)
    if (nextServer) setSelectedServerId(nextServer)
    setError('')
    setInfo(`Selected tool: ${nextName}`)
  }

  async function runTool(event) {
    event.preventDefault()
    setError('')
    setInfo('')
    setResultText('')

    const serverId = String(selectedServerId || '').trim()
    const name = String(toolName || '').trim()
    if (!name) {
      setError('Select a tool name before running.')
      return
    }
    if (isExecuteSqlTool && !advancedSqlMode) {
      setError('execute_sql requires Advanced SQL mode. Enable it before running SQL.')
      return
    }

    let args = {}
    try {
      const parsed = JSON.parse(argsText || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        setError('Arguments must be a JSON object.')
        return
      }
      args = parsed
    } catch (err) {
      setError(`Arguments JSON is invalid: ${err?.message || 'parse error'}`)
      return
    }

    setRunning(true)
    try {
      const res = await fetch(`${API_BASE}/api/v1/mcp/tools/call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...(serverId ? { server_id: serverId } : {}),
          tool_name: name,
          arguments: args,
          advanced_mode: Boolean(advancedSqlMode)
        })
      })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) {
        const code = payload?.error?.code ? ` [${payload.error.code}]` : ''
        const message = payload?.error?.message || `Tool call failed (${res.status})`
        const details = payload?.error?.details ? `\n${prettyJson(payload.error.details)}` : ''
        throw new Error(`${message}${code}${details}`)
      }
      setResultText(prettyJson(payload))
      const latency = payload?.latency_ms?.tools_call
      setInfo(`Tool call succeeded${typeof latency === 'number' ? ` (${latency} ms)` : ''}.`)
    } catch (err) {
      setError(err?.message || 'Tool call failed.')
    } finally {
      setRunning(false)
    }
  }

  return (
    <section className="toolExplorerSection" aria-label="tool-explorer">
      <div className="toolExplorerHead">
        <h2>Tool Explorer</h2>
        <button type="button" className="btn ghost" onClick={loadTools} disabled={loadingTools || loadingServers}>
          {loadingTools ? 'Refreshing...' : 'Refresh catalog'}
        </button>
      </div>

      <div className="toolFilterRow">
        <label>
          Server filter
          <select value={selectedServerId} onChange={(event) => setSelectedServerId(event.target.value)}>
            <option value="">All enabled servers</option>
            {servers.map((server) => (
              <option key={server.id} value={server.id}>
                {server.name} {server.enabled ? '' : '(disabled)'}
              </option>
            ))}
          </select>
        </label>
        <label>
          Search tools
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="search by tool/server/description"
          />
        </label>
      </div>

      {error ? <p className="status error">{error}</p> : null}
      {info ? <p className="status info">{info}</p> : null}

      {catalogErrors.length > 0 ? (
        <div className="toolCatalogErrors">
          <h3>Catalog Errors</h3>
          <ul>
            {catalogErrors.map((item, index) => (
              <li key={`${item?.server_id || 'srv'}-${index}`}>
                {item?.server_name || item?.server_id || 'server'}: {item?.error?.message || 'unknown error'}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="toolCatalog">
        {filteredTools.length === 0 ? <p className="empty">No tools found for current filter.</p> : null}
        {filteredTools.map((tool) => (
          <article key={`${tool.server_id}:${tool.name}`} className="toolCard">
            <div className="toolCardTop">
              <h3>{tool.name}</h3>
              <span className="badge enabled">{tool.server_name}</span>
            </div>
            <p className="description">{tool.description || 'No description provided.'}</p>
            <button type="button" className="btn ghost" onClick={() => selectTool(tool)}>
              Use this tool
            </button>
          </article>
        ))}
      </div>

      <form className="toolRunForm" onSubmit={runTool}>
        <h3>Run Tool</h3>
        <label>
          Server
          <select value={selectedServerId} onChange={(event) => setSelectedServerId(event.target.value)}>
            <option value="">Auto-route by capability</option>
            {runServers.map((server) => (
              <option key={server.id} value={server.id}>
                {server.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Tool name
          <input
            value={toolName}
            onChange={(event) => setToolName(event.target.value)}
            placeholder="ckan__search_datasets"
            required
          />
        </label>
        <label>
          Arguments (JSON object)
          <textarea value={argsText} onChange={(event) => setArgsText(event.target.value)} rows={8} />
        </label>
        {isExecuteSqlTool ? (
          <div className="sqlGuardPanel">
            <h4>SQL Safety Guardrails</h4>
            <p>
              `execute_sql` requires Advanced SQL mode and an explicit `LIMIT` within configured max rows.
            </p>
            <label className="checkboxLabel">
              <input
                type="checkbox"
                checked={advancedSqlMode}
                onChange={(event) => setAdvancedSqlMode(event.target.checked)}
              />
              Enable Advanced SQL mode
            </label>
            {sqlPreview.parseError ? (
              <p className="status error">Preview unavailable: {sqlPreview.parseError}</p>
            ) : (
              <pre className="sqlPreview">{sqlPreview.query || '(No query/sql field found yet)'}</pre>
            )}
          </div>
        ) : null}
        <div className="formActions">
          <button type="submit" className="btn" disabled={running}>
            {running ? 'Running...' : 'Run tool'}
          </button>
        </div>
      </form>

      {resultText ? (
        <div className="toolResultPanel">
          <h3>Result</h3>
          <pre>{resultText}</pre>
        </div>
      ) : null}
    </section>
  )
}
