const SESSION_KEY = 'opencontext_federated_session_id'

function fallbackId() {
  return `sess_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

export function getOrCreateSessionId() {
  if (typeof window === 'undefined') return fallbackId()
  const existing = window.localStorage.getItem(SESSION_KEY)
  if (existing && String(existing).trim()) {
    return String(existing).trim()
  }
  const next = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : fallbackId()
  window.localStorage.setItem(SESSION_KEY, next)
  return next
}

export function resetSessionId() {
  if (typeof window === 'undefined') return fallbackId()
  const next = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : fallbackId()
  window.localStorage.setItem(SESSION_KEY, next)
  return next
}

