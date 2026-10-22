import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { FieldTarget } from '../review'
import { commitment, field, group, job } from '../test/fixtures'
import { FieldPanel } from './FieldPanel'

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof api>()),
  correctField: vi.fn(),
  getCrop: vi.fn(),
  getPage: vi.fn(),
}))

const BOX = { page: 0, x0: 100, y0: 50, x1: 300, y1: 90 }
const g = group({
  commitment: commitment({
    id_number: field('000000019', true, {
      raw_value: '00000001 9',
      explanations: ['ספרת הביקורת אינה תקינה', 'נקרא פעם אחת בלבד'],
      bbox: BOX,
    }),
    received_date: field(null),
  }),
})
const ID: FieldTarget = { groupId: g.id, field: 'id_number', item: null }
const DATE: FieldTarget = { groupId: g.id, field: 'received_date', item: null }

function renderPanel(target: FieldTarget, overrides: Partial<Parameters<typeof FieldPanel>[0]> = {}) {
  const props = {
    job: job([g]),
    group: g,
    target,
    senders: ['כללית', 'מכבי'],
    onSaved: vi.fn(),
    onNavigate: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  }
  render(<FieldPanel {...props} />)
  return props
}

beforeEach(() => {
  vi.mocked(api.getCrop).mockResolvedValue({ kind: 'image', blob: new Blob() })
  vi.mocked(api.getPage).mockResolvedValue({ kind: 'image', blob: new Blob() })
})

describe('FieldPanel', () => {
  it('shows the value as read, the reasons, the crop and the page', async () => {
    renderPanel(ID)
    expect(screen.getByText('00000001 9').getAttribute('dir')).toBe('ltr')
    expect(screen.getByText('ספרת הביקורת אינה תקינה')).toBeTruthy()
    expect(await screen.findByAltText('חיתוך של ת.ז.')).toBeTruthy()
    expect(await screen.findByAltText('עמוד 1 של הטופס')).toBeTruthy()
    expect(api.getCrop).toHaveBeenCalledWith('j1', g.id, 'id_number', null)
    expect(api.getPage).toHaveBeenCalledWith('j1', g.id, 0)
  })

  it('outlines the field on the full page', async () => {
    renderPanel(ID)
    const page = await screen.findByAltText('עמוד 1 של הטופס')
    Object.defineProperty(page, 'naturalWidth', { value: 1000 })
    Object.defineProperty(page, 'naturalHeight', { value: 500 })
    page.dispatchEvent(new Event('load'))
    const highlight = await waitFor(() => {
      const box = document.querySelector<HTMLElement>('.highlight')
      if (!box) throw new Error('no highlight yet')
      return box
    })
    expect([highlight.style.left, highlight.style.top, highlight.style.width, highlight.style.height]).toEqual([
      '10%',
      '10%',
      '20%',
      '8%',
    ])
  })

  it('confirms with Enter: sends the value and hands back the job', async () => {
    const user = userEvent.setup()
    const saved = job([g])
    vi.mocked(api.correctField).mockResolvedValue(saved)
    const props = renderPanel(ID)
    const input = screen.getByRole('textbox', { name: 'ערך' })
    expect(document.activeElement).toBe(input)
    await user.clear(input)
    await user.type(input, '000000026{Enter}')
    expect(api.correctField).toHaveBeenCalledWith('j1', g.id, 'id_number', '000000026', null)
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith(saved, ID))
  })

  it('confirms an unchanged value as it is', async () => {
    const user = userEvent.setup()
    vi.mocked(api.correctField).mockResolvedValue(job([g]))
    renderPanel(ID)
    await user.keyboard('{Enter}')
    expect(api.correctField).toHaveBeenCalledWith('j1', g.id, 'id_number', '000000019', null)
  })

  it('sends a day-first date as YYYY-MM-DD and refuses an unclear one', async () => {
    const user = userEvent.setup()
    vi.mocked(api.correctField).mockResolvedValue(job([g]))
    renderPanel(DATE)
    const input = screen.getByRole('textbox', { name: 'ערך' })
    await user.type(input, '4/1/26{Enter}')
    expect(screen.getByRole('alert').textContent).toContain('DD/MM/YYYY')
    expect(api.correctField).not.toHaveBeenCalled()
    await user.clear(input)
    await user.type(input, '04/01/2026{Enter}')
    expect(api.correctField).toHaveBeenCalledWith('j1', g.id, 'received_date', '2026-01-04', null)
  })

  it('moves to the next orange field with Tab and back with Shift+Tab, closes with Esc', async () => {
    const user = userEvent.setup()
    const props = renderPanel(ID)
    await user.keyboard('{Tab}')
    expect(props.onNavigate).toHaveBeenLastCalledWith(1)
    await user.keyboard('{Shift>}{Tab}{/Shift}')
    expect(props.onNavigate).toHaveBeenLastCalledWith(-1)
    await user.keyboard('{Escape}')
    expect(props.onClose).toHaveBeenCalled()
    expect(api.correctField).not.toHaveBeenCalled()
  })

  it('shows a rejected value in Hebrew and keeps the panel open', async () => {
    const user = userEvent.setup()
    vi.mocked(api.correctField).mockRejectedValue(
      new api.ApiError(422, 'sender must be a canonical name from the closed list'),
    )
    const props = renderPanel({ groupId: g.id, field: 'sender', item: null })
    const input = screen.getByRole('combobox', { name: 'ערך' })
    await user.clear(input)
    await user.type(input, 'גוף אחר{Enter}')
    expect((await screen.findByRole('alert')).textContent).toBe('השמירה נכשלה: יש לבחור גורם שולח מהרשימה')
    expect(props.onSaved).not.toHaveBeenCalled()
  })

  it('says so when the page image is gone and when a field has no box', async () => {
    vi.mocked(api.getPage).mockResolvedValue({ kind: 'missing', detail: api.IMAGE_GONE })
    renderPanel(DATE)
    expect(await screen.findByText('התמונה כבר לא זמינה (פג תוקף הקבצים הזמניים)')).toBeTruthy()
    expect(screen.getByText('למודל אין מיקום של השדה בעמוד')).toBeTruthy()
    expect(api.getCrop).not.toHaveBeenCalled()
  })

  it('is read-only while the job is still running', () => {
    renderPanel(ID, { job: job([g], { status: 'running' }) })
    expect((screen.getByRole('textbox', { name: 'ערך' }) as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByText('אפשר לתקן רק אחרי שהעיבוד הסתיים')).toBeTruthy()
  })
})
