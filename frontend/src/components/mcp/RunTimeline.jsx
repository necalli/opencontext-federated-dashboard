import { useEffect, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5100'

function formatTimestamp(value) {
  const text = String(value || '').trim()
  if (!text) return '-'
  try {
    return new Date(text).toLocaleString()
  } catch {
    return text
  }
}

function summarizeEvent(event) {
  const phase = String(event?.phase || '').trim()
  if (phase === 'tool_use') {
    return `Tool call: ${event.tool_name || 'unknown'}`
  }
  if (phase === 'tool_result') {
    return `Tool result${event.is_error ? ' (error)' : ''}`
  }
  if (phase === 'server_result') {
    return `Server ${event.server_name || 'unknown'}: ${event.ok ? 'ok' : 'error'}`
  }
  return phase || 'event'
}

export default function RunTimeline() {
  const [runs, setRuns] = useState([])
  const [selectedRunId, setSelectedRunId] = useState('')
  const [selectedRun, setSelectedRun] = useState(null)
  const [sessionFilter, setSessionFilter] = useState('')
  const [loadingList, setLoadingList] = useState(false)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  async function loadRuns() {
    setLoadingList(true)
    setError('')
    setInfo('')
    try {
      const query = new URLSearchParams()
      query.set('limit', '40')
      if (sessionFilter.trim()) query.set('session_id', sessionFilter.trim())
      const res = await fetch(`${API_BASE}/api/v1/runs?${query.toString()}`)
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(payload?.error?.message || `Run list failed (${res.status})`)
      }
      const rows = Array.isArray(payload?.runs) ? payload.runs : []
      setRuns(rows)
      setInfo(`Loaded ${rows.length} run trace(s).`)
      if (rows.length > 0 && !selectedRunId) {
        setSelectedRunId(String(rows[0].run_id || ''))
      }
    } catch (err) {
      setRuns([])
      setSelectedRunId('')
      setSelectedRun(null)
      setError(err?.message || 'Failed to load run traces.')
    } finally {
      setLoadingList(false)
    }
  }

  async function loadRunDetail(runId) {
    const id = String(runId || '').trim()
    if (!id) {
      setSelectedRun(null)
      return
    }
    setLoadingDetail(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/api/v1/runs/${encodeURIComponent(id)}`)
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(payload?.error?.message || `Run detail failed (${res.status})`)
      }
      const row = payload?.run && typeof payload.run === 'object' ? payload.run : null
      setSelectedRun(row)
    } catch (err) {
      setSelectedRun(null)
      setError(err?.message || 'Failed to load run detail.')
    } finally {
      setLoadingDetail(false)
    }
  }

  useEffect(() => {
    loadRuns()
  }, [])

  useEffect(() => {
    loadRunDetail(selectedRunId)
  }, [selectedRunId])

  return (
    <section className="runTimelineSection" aria-label="run-timeline">
      <div className="runTimelineHead">
        <h2>Run Timeline</h2>
        <button type="button" className="btn ghost" onClick={loadRuns} disabled={loadingList || loadingDetail}>
          {loadingList ? 'Refreshing...' : 'Refresh runs'}
        </button>
      </div>

      <div className="runTimelineFilter">
        <label>
          Session filter (optional)
          <input
            value={sessionFilter}
            onChange={(event) => setSessionFilter(event.target.value)}
            placeholder="session id"
          />
        </label>
        <button type="button" className="btn ghost" onClick={loadRuns} disabled={loadingList || loadingDetail}>
          Apply filter
        </button>
      </div>

      {error ? <p className="status error">{error}</p> : null}
      {info ? <p className="status info">{info}</p> : null}

      <div className="runTimelineGrid">
        <div className="runListPanel">
          {runs.length === 0 ? <p className="empty">No runs captured yet.</p> : null}
          {runs.map((run) => (
            <button
              key={run.run_id}
              type="button"
              className={`runListItem ${selectedRunId === run.run_id ? 'selected' : ''}`}
              onClick={() => setSelectedRunId(run.run_id)}
            >
              <div className="runListTop">
                <strong>{run.runtime || 'runtime'}</strong>
                <span className={`badge ${run.status === 'completed' ? 'enabled' : 'disabled'}`}>
                  {run.status || 'unknown'}
                </span>
              </div>
              <p>{run.message_preview || '(no message)'}</p>
              <small>
                {formatTimestamp(run.created_at)} ?? {run.duration_ms || 0} ms ?? events: {run.tool_event_count || 0}
              </small>
            </button>
          ))}
        </div>

        <div className="runDetailPanel">
          {loadingDetail ? <p className="empty">Loading run detail...</p> : null}
          {!loadingDetail && !selectedRun ? <p className="empty">Select a run to inspect details.</p> : null}
          {!loadingDetail && selectedRun ? (
            <>
              <div className="runDetailHead">
                <h3>Run Detail</h3>
                <a
                  className="btn ghost"
                  href={`${API_BASE}/api/v1/runs/${encodeURIComponent(selectedRun.run_id)}/export`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Export JSON
                </a>
              </div>

              <div className="runMetaGrid">
                <p><strong>Run ID:</strong> {selectedRun.run_id}</p>
                <p><strong>Endpoint:</strong> {selectedRun.endpoint}</p>
                <p><strong>Session:</strong> {selectedRun.session_id || '-'}</p>
                <p><strong>Status:</strong> {selectedRun.status}</p>
                <p><strong>Created:</strong> {formatTimestamp(selectedRun.created_at)}</p>
                <p><strong>Completed:</strong> {formatTimestamp(selectedRun.completed_at)}</p>
                <p><strong>Duration:</strong> {selectedRun.duration_ms || 0} ms</p>
                <p><strong>Runtime:</strong> {selectedRun?.response?.runtime || '-'}</p>
              </div>

              <div className="runBlock">
                <h4>Request</h4>
                <pre>{JSON.stringify(selectedRun.request || {}, null, 2)}</pre>
              </div>
              <div className="runBlock">
                <h4>Response Summary</h4>
                <pre>{JSON.stringify(selectedRun.response || {}, null, 2)}</pre>
              </div>
              <div className="runBlock">
                <h4>Tool Timeline</h4>
                {Array.isArray(selectedRun.tool_events) && selectedRun.tool_events.length > 0 ? (
                  <ul className="runEventList">
                    {selectedRun.tool_events.map((event, index) => (
                      <li key={`${index}-${event?.phase || event?.tool_use_id || ''}`}>
                        {index + 1}. {summarizeEvent(event)}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="empty">No tool events recorded.</p>
                )}
              </div>

              {Array.isArray(selectedRun.errors) && selectedRun.errors.length > 0 ? (
                <div className="runBlock">
                  <h4>Errors</h4>
                  <pre>{JSON.stringify(selectedRun.errors, null, 2)}</pre>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
    </section>
  )
}
