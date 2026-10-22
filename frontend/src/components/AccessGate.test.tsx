import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { loadToken, reportAccessDenied } from '../access'
import * as api from '../api'
import { AccessGate } from './AccessGate'
import { DEMO_BANNER_TEXT, DemoBanner } from './DemoBanner'

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof api>()),
  checkAccess: vi.fn(),
}))

const TOKEN = 'dummy-demo-token-0123456789'
const refused = new api.ApiError(401, 'access token missing or invalid')

function renderGate() {
  render(
    <AccessGate>
      <p>האפליקציה</p>
    </AccessGate>,
  )
}

beforeEach(() => sessionStorage.clear())

describe('AccessGate', () => {
  it('opens at once when the server needs no code', async () => {
    vi.mocked(api.checkAccess).mockResolvedValue()
    renderGate()
    expect(await screen.findByText('האפליקציה')).toBeTruthy()
    expect(api.checkAccess).toHaveBeenCalledWith(null)
  })

  it('asks for the code, then keeps it in sessionStorage only', async () => {
    vi.mocked(api.checkAccess).mockRejectedValueOnce(refused).mockResolvedValueOnce()
    const user = userEvent.setup()
    renderGate()
    await user.type(await screen.findByLabelText('קוד גישה להדגמה'), `${TOKEN}{Enter}`)
    expect(await screen.findByText('האפליקציה')).toBeTruthy()
    expect(api.checkAccess).toHaveBeenLastCalledWith(TOKEN)
    expect(loadToken()).toBe(TOKEN)
    expect(localStorage.length).toBe(0)
  })

  it('says so when the code is wrong', async () => {
    vi.mocked(api.checkAccess).mockRejectedValue(refused)
    const user = userEvent.setup()
    renderGate()
    await user.type(await screen.findByLabelText('קוד גישה להדגמה'), 'wrong-code-0123456789{Enter}')
    expect((await screen.findByRole('alert')).textContent).toBe('קוד הגישה שגוי')
    expect(loadToken()).toBeNull()
  })

  it('comes back when a later request is refused', async () => {
    vi.mocked(api.checkAccess).mockResolvedValue()
    renderGate()
    await screen.findByText('האפליקציה')
    act(() => reportAccessDenied())
    expect(await screen.findByLabelText('קוד גישה להדגמה')).toBeTruthy()
    expect(screen.queryByText('האפליקציה')).toBeNull()
  })

  it('offers a retry when the server cannot be reached', async () => {
    vi.mocked(api.checkAccess).mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValueOnce()
    const user = userEvent.setup()
    renderGate()
    await user.click(await screen.findByRole('button', { name: 'ניסיון חוזר' }))
    expect(await screen.findByText('האפליקציה')).toBeTruthy()
  })
})

describe('DemoBanner', () => {
  it('warns to upload sample forms only', () => {
    render(<DemoBanner />)
    expect(screen.getByRole('note').textContent).toBe(DEMO_BANNER_TEXT)
    expect(DEMO_BANNER_TEXT).toBe('הדגמה, יש להעלות רק טפסי דוגמה, לא נתוני אמת')
  })
})
