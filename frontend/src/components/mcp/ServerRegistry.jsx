import { useEffect, useMemo, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5100'

const initialForm = {
  name: '',
  endpoint: '',
  description: '',
  enabled: true
}

function extractError(payload, fallback) {
  if (payload?.error?.message) return payload.error.message
  if (typeof payload?.message === 'string' && payload.message) return payload.message
  return fallback
}

export default function ServerRegistry() {
  const [servers, setServers] = useState([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState({})
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [editingId, setEditingId] = useState('')
  const [form, setForm] = useState(initialForm)

  const title = useMemo(() => (editingId ? 'Edit server' : 'Add server'), [editingId])

  async function loadServers() {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/api/v1/mcp/servers`)
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(extractError(payload, `Load failed (${res.status})`))
      setServers(Array.isArray(payload.servers) ? payload.servers : [])
    } catch (err) {
      setError(err.message || 'Failed to load server registry.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadServers()
  }, [])

  function resetForm() {
    setEditingId('')
    setForm(initialForm)
  }

  function onChange(event) {
    const { name, value, type, checked } = event.target
    setForm((current) => ({
      ...current,
      [name]: type === 'checkbox' ? checked : value
    }))
  }

  async function onSubmit(event) {
    event.preventDefault()
    setSaving(true)
    setError('')
    setInfo('')

    const body = {
      name: form.name.trim(),
      endpoint: form.endpoint.trim(),
      description: form.description.trim(),
      enabled: Boolean(form.enabled)
    }

    const target = editingId
      ? `${API_BASE}/api/v1/mcp/servers/${editingId}`
      : `${API_BASE}/api/v1/mcp/servers`
    const method = editingId ? 'PUT' : 'POST'

    try {
      const res = await fetch(target, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(extractError(payload, `Save failed (${res.status})`))
      setInfo(editingId ? 'Server updated.' : 'Server created.')
      resetForm()
      await loadServers()
    } catch (err) {
      setError(err.message || 'Failed to save server.')
    } finally {
      setSaving(false)
    }
  }

  function startEdit(server) {
    setEditingId(server.id)
    setForm({
      name: server.name || '',
      endpoint: server.endpoint || '',
      description: server.description || '',
      enabled: Boolean(server.enabled)
    })
    setError('')
    setInfo('')
  }

  async function removeServer(server) {
    const ok = window.confirm(`Delete server "${server.name}"?`)
    if (!ok) return

    setError('')
    setInfo('')
    try {
      const res = await fetch(`${API_BASE}/api/v1/mcp/servers/${server.id}`, { method: 'DELETE' })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(extractError(payload, `Delete failed (${res.status})`))
      if (editingId === server.id) resetForm()
      setInfo('Server deleted.')
      await loadServers()
    } catch (err) {
      setError(err.message || 'Failed to delete server.')
    }
  }

  async function testConnection(server) {
    setTesting((current) => ({ ...current, [server.id]: true }))
    setError('')
    setInfo('')
    try {
      const res = await fetch(`${API_BASE}/api/v1/mcp/servers/${server.id}/test`, { method: 'POST' })
      const payload = await res.json().catch(() => ({}))
      if (payload?.ok) {
        setInfo(`Connection passed for ${server.name}.`)
      } else {
        const message = payload?.error?.message || `Connection failed (${res.status})`
        const stage = payload?.stage ? ` Stage: ${payload.stage}.` : ''
        setError(`${message}${stage}`)
      }
      await loadServers()
    } catch (err) {
      setError(err.message || 'Failed to test connection.')
    } finally {
      setTesting((current) => ({ ...current, [server.id]: false }))
    }
  }

  return (
    <section className="registrySection" aria-label="mcp-server-registry">
      <div className="registryHead">
        <h2>MCP Server Registry</h2>
        <button type="button" className="btn ghost" onClick={loadServers} disabled={loading}>
          {loading ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      <form className="registryForm" onSubmit={onSubmit}>
        <h3>{title}</h3>
        <label>
          Name
          <input name="name" value={form.name} onChange={onChange} placeholder="Boston Open Data" required />
        </label>
        <label>
          Endpoint
          <input
            name="endpoint"
            value={form.endpoint}
            onChange={onChange}
            placeholder="https://your-mcp-host/mcp"
            required
          />
        </label>
        <label>
          Description
          <textarea
            name="description"
            value={form.description}
            onChange={onChange}
            rows={2}
            placeholder="One plugin per OpenContext server"
          />
        </label>
        <label className="checkboxLabel">
          <input type="checkbox" name="enabled" checked={form.enabled} onChange={onChange} />
          Enabled
        </label>

        <div className="formActions">
          <button type="submit" className="btn" disabled={saving}>
            {saving ? 'Saving...' : editingId ? 'Update server' : 'Add server'}
          </button>
          {editingId ? (
            <button type="button" className="btn ghost" onClick={resetForm}>
              Cancel
            </button>
          ) : null}
        </div>
      </form>

      {error ? <p className="status error">{error}</p> : null}
      {info ? <p className="status info">{info}</p> : null}

      <div className="registryList">
        {servers.length === 0 ? <p className="empty">No servers registered yet.</p> : null}
        {servers.map((server) => {
          const lastTest = server.last_test
          const testState = lastTest?.ok === true ? 'pass' : lastTest?.ok === false ? 'fail' : 'none'
          return (
            <article key={server.id} className="serverCard">
              <div className="serverTop">
                <h3>{server.name}</h3>
                <span className={`badge ${server.enabled ? 'enabled' : 'disabled'}`}>
                  {server.enabled ? 'enabled' : 'disabled'}
                </span>
              </div>
              <p className="endpoint">{server.endpoint}</p>
              {server.description ? <p className="description">{server.description}</p> : null}
              {lastTest ? (
                <p className={`testResult ${testState}`}>
                  Last test: {lastTest.ok ? 'passed' : 'failed'}
                  {lastTest.stage ? ` (${lastTest.stage})` : ''}
                </p>
              ) : (
                <p className="testResult">Last test: not run</p>
              )}
              <div className="cardActions">
                <button type="button" className="btn" onClick={() => testConnection(server)} disabled={Boolean(testing[server.id])}>
                  {testing[server.id] ? 'Testing...' : 'Test connection'}
                </button>
                <button type="button" className="btn ghost" onClick={() => startEdit(server)}>
                  Edit
                </button>
                <button type="button" className="btn danger" onClick={() => removeServer(server)}>
                  Delete
                </button>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
