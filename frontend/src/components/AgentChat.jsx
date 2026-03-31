import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'
import { getOrCreateSessionId, resetSessionId } from '../lib/session'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5100'
const CHAT_WIDTH_KEY = 'opencontext-chat-width'

const initialMessage = {
  id: 'assistant-initial',
  role: 'assistant',
  content:
    'Chat runtime is online. Ask for datasets, schema details, or civic-data workflows and I will use MCP tools when available.',
  meta: null,
  pending: false
}

const markdownSchema = {
  ...defaultSchema,
  tagNames: [
    ...(defaultSchema.tagNames || []),
    'table',
    'thead',
    'tbody',
    'tr',
    'th',
    'td'
  ],
  attributes: {
    ...(defaultSchema.attributes || {}),
    a: [...((defaultSchema.attributes && defaultSchema.attributes.a) || []), 'target', 'rel'],
    code: [...((defaultSchema.attributes && defaultSchema.attributes.code) || []), 'className'],
    th: [...((defaultSchema.attributes && defaultSchema.attributes.th) || []), 'align'],
    td: [...((defaultSchema.attributes && defaultSchema.attributes.td) || []), 'align']
  }
}

const markdownComponents = {
  a(props) {
    const href = String(props?.href || '')
    const external = /^https?:\/\//i.test(href)
    return (
      <a
        {...props}
        target={external ? '_blank' : '_self'}
        rel={external ? 'noopener noreferrer' : undefined}
      />
    )
  }
}

function parseSseFrames(buffer) {
  const normalized = String(buffer || '').replace(/\r\n/g, '\n')
  const frames = normalized.split('\n\n')
  const remainder = frames.pop() || ''
  return { frames, remainder }
}

function parseTrailingFrame(buffer) {
  const raw = String(buffer || '').trim()
  if (!raw) return []
  return [raw]
}

function parseSseEvent(rawFrame) {
  const lines = String(rawFrame || '').split('\n')
  let eventName = 'message'
  const dataLines = []

  for (const line of lines) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim() || 'message'
      continue
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }
  if (!dataLines.length) return null
  try {
    return {
      eventName,
      payload: JSON.parse(dataLines.join('\n'))
    }
  } catch {
    return null
  }
}

function getStoredWidth() {
  if (typeof window === 'undefined') return 460
  const raw = window.localStorage.getItem(CHAT_WIDTH_KEY)
  const parsed = Number.parseInt(raw || '460', 10)
  if (!Number.isFinite(parsed)) return 460
  return Math.min(Math.max(parsed, 360), 920)
}

export default function AgentChat({ launchSignal = 0, onVisualization = null }) {
  const [open, setOpen] = useState(false)
  const [panelWidth, setPanelWidth] = useState(() => getStoredWidth())
  const [sessionId, setSessionId] = useState(() => getOrCreateSessionId())
  const [messages, setMessages] = useState([initialMessage])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const resizeRef = useRef({ startX: 0, startWidth: 460 })
  const resizingRef = useRef(false)
  const [resizing, setResizing] = useState(false)
  const bottomRef = useRef(null)
  const progressMarkersRef = useRef(new Set())

  const latestAssistantMeta = useMemo(() => {
    const ordered = [...messages].reverse()
    const found = ordered.find((item) => item.role === 'assistant' && item.meta)
    return found ? found.meta : null
  }, [messages])

  useEffect(() => {
    if (!launchSignal) return
    setOpen(true)
  }, [launchSignal])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(CHAT_WIDTH_KEY, String(panelWidth))
  }, [panelWidth])

  useEffect(() => {
    if (!open) return
    const handle = window.setTimeout(() => {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }, 40)
    return () => window.clearTimeout(handle)
  }, [messages, open])

  useEffect(() => {
    return () => {
      window.removeEventListener('pointermove', handleResizeMove)
      window.removeEventListener('pointerup', handleResizeEnd)
    }
  }, [])

  function handleResizeStart(event) {
    event.preventDefault()
    resizingRef.current = true
    setResizing(true)
    resizeRef.current = {
      startX: event.clientX,
      startWidth: panelWidth
    }
    window.addEventListener('pointermove', handleResizeMove)
    window.addEventListener('pointerup', handleResizeEnd)
  }

  function handleResizeMove(event) {
    if (!resizingRef.current) return
    const delta = resizeRef.current.startX - event.clientX
    const next = Math.min(Math.max(resizeRef.current.startWidth + delta, 360), 920)
    setPanelWidth(next)
  }

  function handleResizeEnd() {
    resizingRef.current = false
    setResizing(false)
    window.removeEventListener('pointermove', handleResizeMove)
    window.removeEventListener('pointerup', handleResizeEnd)
  }

  function resetConversation() {
    const nextSessionId = resetSessionId()
    setSessionId(nextSessionId)
    setMessages([initialMessage])
    setError('')
    setStatus('idle')
  }

  function updateAssistantMessage(id, updater) {
    setMessages((current) =>
      current.map((item) => {
        if (item.id !== id) return item
        return typeof updater === 'function' ? updater(item) : item
      })
    )
  }

  function appendAssistantProgress(id, marker, line) {
    const normalizedLine = String(line || '').trim()
    if (!normalizedLine) return
    const key = String(marker || '').trim()
    if (key) {
      if (progressMarkersRef.current.has(key)) return
      progressMarkersRef.current.add(key)
    }
    updateAssistantMessage(id, (item) => {
      const current = String(item.content || '')
      const next = current ? `${current}\n${normalizedLine}` : normalizedLine
      return {
        ...item,
        content: next,
        pending: true
      }
    })
  }

  function publishVisualization(artifact) {
    if (!artifact || typeof artifact !== 'object') return
    if (typeof onVisualization !== 'function') return
    const normalized = {
      ...artifact,
      id: String(artifact.id || '').trim() || `viz_${Date.now()}`,
      title: String(artifact.title || 'Untitled visualization').trim(),
      chart_type: String(artifact.chart_type || 'table').trim().toLowerCase() || 'table',
      summary: String(artifact.summary || '').trim(),
      source: String(artifact.source || '').trim(),
      created_at: String(artifact.created_at || new Date().toISOString()),
    }
    onVisualization(normalized)
  }

  function publishVisualizationsFromMeta(meta) {
    const debug = meta && typeof meta === 'object' && meta.debug && typeof meta.debug === 'object' ? meta.debug : {}
    const agentSdk = debug.agent_sdk && typeof debug.agent_sdk === 'object' ? debug.agent_sdk : {}
    const visualizations = Array.isArray(agentSdk.visualizations) ? agentSdk.visualizations : []
    for (const item of visualizations) {
      publishVisualization(item)
    }
  }

  async function runNonStreamFallback({ message, assistantId }) {
    const response = await fetch(`${API_BASE}/api/v1/agent/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        session_id: sessionId,
        prefer_connector: true
      })
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload?.error || `Chat failed (${response.status})`)
    }
    const text = String(payload?.message || '').trim()
    updateAssistantMessage(assistantId, (item) => ({
      ...item,
      content: text || 'No response text returned.',
      meta: payload?.meta || null,
      pending: false
    }))
    publishVisualizationsFromMeta(payload?.meta || {})
    setStatus('completed_non_stream')
  }

  async function runStream({ message, assistantId }) {
    const response = await fetch(`${API_BASE}/api/v1/agent/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        session_id: sessionId,
        prefer_connector: true
      })
    })
    if (!response.ok) {
      throw new Error(`Stream failed (${response.status})`)
    }
    if (!response.body || typeof response.body.getReader !== 'function') {
      throw new Error('Stream body unavailable')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let assistantText = ''
    let donePayload = null

    function applyEvent(event) {
      if (!event) return
      const { eventName, payload } = event
      if (eventName === 'status') {
        const phase = String(payload?.phase || 'running')
        setStatus(phase)
        if (phase === 'runtime_started') {
          appendAssistantProgress(assistantId, `status:${phase}`, `[step: ${phase}]`)
        }
        return
      }
      if (eventName === 'tool_progress') {
        const phase = String(payload?.phase || '').trim()
        const toolName = String(payload?.tool_name || payload?.tool_use_id || '').trim()
        if (phase === 'tool_use') {
          appendAssistantProgress(
            assistantId,
            `tool_use:${toolName}:${String(payload?.tool_use_id || '').trim()}`,
            `[using tool: ${toolName || 'tool'}]`
          )
          return
        }
        if (phase === 'tool_result') {
          appendAssistantProgress(
            assistantId,
            `tool_result:${toolName}:${String(payload?.tool_use_id || '').trim()}:${payload?.is_error ? 'error' : 'ok'}`,
            payload?.is_error ? `[tool error: ${toolName || 'tool'}]` : `[tool completed: ${toolName || 'tool'}]`
          )
          return
        }
        return
      }
      if (eventName === 'delta') {
        const piece = String(payload?.text || '')
        if (!piece) return
        assistantText += piece
        updateAssistantMessage(assistantId, (item) => ({
          ...item,
          content: `${item.content || ''}${piece}`,
          pending: true
        }))
        return
      }
      if (eventName === 'visualization') {
        const artifact =
          payload && typeof payload === 'object' && payload.artifact && typeof payload.artifact === 'object'
            ? payload.artifact
            : payload
        publishVisualization(artifact)
        const title = String(artifact?.title || '').trim() || 'visualization'
        appendAssistantProgress(
          assistantId,
          `visualization:${String(artifact?.id || title).trim()}`,
          `[visualization ready: ${title}]`
        )
        return
      }
      if (eventName === 'error') {
        throw new Error(String(payload?.error || 'Streaming error'))
      }
      if (eventName === 'done') {
        donePayload = payload
        publishVisualizationsFromMeta(payload?.meta || {})
      }
    }

    while (true) {
      const { value, done } = await reader.read()
      if (done) {
        buffer += decoder.decode()
        const parsed = parseSseFrames(buffer)
        buffer = parsed.remainder
        for (const frame of parsed.frames) {
          applyEvent(parseSseEvent(frame))
        }
        for (const frame of parseTrailingFrame(buffer)) {
          applyEvent(parseSseEvent(frame))
        }
        break
      }
      buffer += decoder.decode(value, { stream: true })
      const parsed = parseSseFrames(buffer)
      buffer = parsed.remainder
      for (const frame of parsed.frames) {
        applyEvent(parseSseEvent(frame))
      }
    }

    const finalText = String(donePayload?.message || assistantText || '').trim()
    updateAssistantMessage(assistantId, (item) => ({
      ...item,
      content: finalText || 'No response text returned.',
      meta: donePayload?.meta || null,
      pending: false
    }))
    setStatus('completed')
  }

  function onComposerKeyDown(event) {
    if (event.key !== 'Enter') return
    if (event.shiftKey) return
    if (event.nativeEvent?.isComposing) return
    event.preventDefault()
    if (loading || !input.trim()) return
    event.currentTarget.form?.requestSubmit()
  }

  async function onSubmit(event) {
    event.preventDefault()
    const trimmed = input.trim()
    if (!trimmed || loading) return

    const userMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: trimmed,
      meta: null,
      pending: false
    }
    const assistantId = `assistant-${Date.now()}`
    const assistantShell = {
      id: assistantId,
      role: 'assistant',
      content: '',
      meta: null,
      pending: true
    }

    setMessages((current) => [...current, userMessage, assistantShell])
    setInput('')
    setLoading(true)
    setError('')
    setStatus('accepted')
    progressMarkersRef.current = new Set()

    try {
      try {
        await runStream({ message: trimmed, assistantId })
      } catch (streamError) {
        setStatus('stream_failed_fallback')
        await runNonStreamFallback({ message: trimmed, assistantId })
      }
    } catch (err) {
      updateAssistantMessage(assistantId, (item) => ({
        ...item,
        content: 'Request failed before a valid response was returned.',
        pending: false
      }))
      setError(err?.message || 'Chat request failed.')
      setStatus('error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      {!open ? (
        <button
          type="button"
          className="chatLauncher"
          onClick={() => setOpen(true)}
        >
          AI Chat
        </button>
      ) : null}

      <div
        className={`chatDrawerBackdrop ${open ? 'open' : ''}`}
        onClick={() => setOpen(false)}
        aria-hidden={open ? 'false' : 'true'}
      />

      <aside
        className={`chatDrawer ${open ? 'open' : ''}`}
        style={{ width: `${panelWidth}px` }}
        aria-hidden={open ? 'false' : 'true'}
      >
        <div
          className="chatResizeHandle"
          onPointerDown={handleResizeStart}
          style={{ touchAction: 'none' }}
        >
          <div className={`chatResizeBar ${resizing ? 'active' : ''}`} />
        </div>

        <section className="chatSection drawerMode" aria-label="agent-chat">
          <div className="chatHeader">
            <h2>Agent Chat</h2>
            <div className="chatHeaderActions">
              <span className="sessionLabel" title={sessionId}>
                Session: {sessionId.slice(0, 12)}...
              </span>
              <button type="button" className="btn ghost" onClick={resetConversation} disabled={loading}>
                New session
              </button>
              <button type="button" className="btn ghost" onClick={() => setOpen(false)}>
                Close
              </button>
            </div>
          </div>

          <div className="statusBar">
            <span className={`statusChip status-${status}`}>Status: {status}</span>
            {latestAssistantMeta ? (
              <span className="runtimeChip">
                Runtime: {latestAssistantMeta.runtime}
                {latestAssistantMeta.fallback_used ? ' (fallback)' : ''}
              </span>
            ) : null}
          </div>

          <div className="chatFeed">
            {messages.map((item) => (
              <article key={item.id} className={`bubble ${item.role === 'assistant' ? 'assistant' : 'user'}`}>
                <header>
                  <strong>{item.role === 'assistant' ? 'Assistant' : 'You'}</strong>
                  {item.pending ? <span className="pendingTag">streaming...</span> : null}
                </header>
                <div className="bubbleBody">
                  {item.role === 'assistant' ? (
                    <div className="markdownBody">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm, remarkBreaks]}
                        rehypePlugins={[[rehypeSanitize, markdownSchema]]}
                        components={markdownComponents}
                      >
                        {item.content || '...'}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <div className="plainBody">{item.content || '...'}</div>
                  )}
                </div>
              </article>
            ))}
            <div ref={bottomRef} />
          </div>

          {error ? <p className="status error">{error}</p> : null}

          <form className="chatComposer" onSubmit={onSubmit}>
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={onComposerKeyDown}
              rows={3}
              placeholder="Ask for datasets, schema details, or analysis workflows..."
              disabled={loading}
            />
            <div className="composerActions">
              <button type="submit" className="btn" disabled={loading || !input.trim()}>
                {loading ? 'Working...' : 'Send'}
              </button>
            </div>
          </form>
        </section>
      </aside>
    </>
  )
}
