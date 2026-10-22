// The demo access code: kept in sessionStorage only (gone when the tab closes), never in
// localStorage, a cookie or the URL bar. The backend checks it on every request.

const STORAGE_KEY = 'ce-access-token'

type Listener = () => void
const deniedListeners = new Set<Listener>()

export function loadToken(): string | null {
  return sessionStorage.getItem(STORAGE_KEY)
}

export function saveToken(token: string): void {
  sessionStorage.setItem(STORAGE_KEY, token)
}

export function clearToken(): void {
  sessionStorage.removeItem(STORAGE_KEY)
}

// The server refused the code (401): it is dropped and the listeners (the access screen) are told.
export function reportAccessDenied(): void {
  clearToken()
  for (const listener of deniedListeners) listener()
}

export function onAccessDenied(listener: Listener): () => void {
  deniedListeners.add(listener)
  return () => deniedListeners.delete(listener)
}
