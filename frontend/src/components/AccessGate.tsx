import { type FormEvent, type ReactNode, useEffect, useState } from 'react'
import { clearToken, loadToken, onAccessDenied, saveToken } from '../access'
import { ApiError, checkAccess } from '../api'
import { reasonText } from '../messages'

type GateState =
  | { kind: 'checking' }
  | { kind: 'locked'; error: string | null }
  | { kind: 'offline'; error: string }
  | { kind: 'open' }

// A sleeping demo server (Render free plan) can take about a minute to answer the first request.
const CHECKING_TEXT = 'מתחבר לשרת… בהפעלה ראשונה זה עשוי להימשך עד דקה'
const WRONG_CODE_TEXT = 'קוד הגישה שגוי'
const EXPIRED_CODE_TEXT = 'יש להזין שוב את קוד הגישה'

// Asks the server whether `token` (or none) is accepted; a refused token is dropped.
async function verify(token: string | null, refusedText: string): Promise<GateState> {
  try {
    await checkAccess(token)
    if (token) saveToken(token)
    return { kind: 'open' }
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      clearToken()
      return { kind: 'locked', error: token ? refusedText : null }
    }
    return { kind: 'offline', error: reasonText(e) }
  }
}

// Shows `children` once the server accepts the stored code (or needs none); otherwise asks for it.
// A 401 later on (api.ts reports it) brings the screen back.
export function AccessGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GateState>({ kind: 'checking' })
  const [code, setCode] = useState('')

  useEffect(() => {
    let active = true
    void verify(loadToken(), EXPIRED_CODE_TEXT).then((next) => active && setState(next))
    const unsubscribe = onAccessDenied(() => setState({ kind: 'locked', error: EXPIRED_CODE_TEXT }))
    return () => {
      active = false
      unsubscribe()
    }
  }, [])

  function recheck(token: string | null, refusedText: string) {
    setState({ kind: 'checking' })
    void verify(token, refusedText).then(setState)
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    const token = code.trim()
    if (token) recheck(token, WRONG_CODE_TEXT)
  }

  if (state.kind === 'open') return children
  return (
    <main className="access-gate">
      <h1>חילוץ התחייבויות</h1>
      {state.kind === 'checking' && <p role="status">{CHECKING_TEXT}</p>}
      {state.kind === 'offline' && (
        <>
          <p className="error" role="alert">
            {state.error}
          </p>
          <button type="button" onClick={() => recheck(loadToken(), EXPIRED_CODE_TEXT)}>
            ניסיון חוזר
          </button>
        </>
      )}
      {state.kind === 'locked' && (
        <form onSubmit={submit}>
          <label htmlFor="access-code">קוד גישה להדגמה</label>
          <input
            id="access-code"
            type="password"
            autoComplete="off"
            autoFocus
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          <button type="submit" disabled={!code.trim()}>
            כניסה
          </button>
          {state.error && (
            <p className="error" role="alert">
              {state.error}
            </p>
          )}
          <p className="muted small">הקוד נשמר בלשונית זו בלבד ונמחק כשסוגרים אותה.</p>
        </form>
      )}
    </main>
  )
}
