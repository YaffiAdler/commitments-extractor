import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { loadToken, onAccessDenied, saveToken } from './access'
import { ApiError, getJob, streamJob } from './api'
import { job } from './test/fixtures'

const TOKEN = 'dummy-demo-token-0123456789'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

class FakeEventSource {
  static last: FakeEventSource | null = null
  readonly url: string
  onerror: (() => void) | null = null
  constructor(url: string) {
    this.url = url
    FakeEventSource.last = this
  }
  addEventListener() {}
  close() {}
}

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('access code', () => {
  it('is sent as a Bearer header', async () => {
    saveToken(TOKEN)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(job([])))
    vi.stubGlobal('fetch', fetchMock)
    await getJob('j1')
    const headers = new Headers(fetchMock.mock.calls[0][1].headers)
    expect(headers.get('Authorization')).toBe(`Bearer ${TOKEN}`)
  })

  it('is left out when there is none', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(job([])))
    vi.stubGlobal('fetch', fetchMock)
    await getJob('j1')
    expect(new Headers(fetchMock.mock.calls[0][1].headers).has('Authorization')).toBe(false)
  })

  it('goes in the query of the event stream, which cannot send headers', () => {
    saveToken(TOKEN)
    vi.stubGlobal('EventSource', FakeEventSource)
    void streamJob('j1', { onProgress: () => undefined, onRows: () => undefined })
    expect(FakeEventSource.last?.url).toBe(`/api/jobs/j1/events?access_token=${TOKEN}`)
  })

  it('is dropped on 401 and the access screen is told', async () => {
    saveToken(TOKEN)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'access token missing or invalid' }, 401)))
    const denied = vi.fn()
    const unsubscribe = onAccessDenied(denied)
    await expect(getJob('j1')).rejects.toBeInstanceOf(ApiError)
    unsubscribe()
    expect(denied).toHaveBeenCalledOnce()
    expect(loadToken()).toBeNull()
  })
})
