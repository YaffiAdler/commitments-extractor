import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import * as api from './api'
import { commitment, field, group, job } from './test/fixtures'

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof api>()),
  uploadFiles: vi.fn(),
  streamJob: vi.fn(),
  getJob: vi.fn(),
  getSenders: vi.fn(),
  correctField: vi.fn(),
  getCrop: vi.fn(),
  getPage: vi.fn(),
  exportJob: vi.fn(),
  deleteJob: vi.fn(),
}))

const first = group({
  commitment: commitment({ id_number: field('000000019', true), last_name: field('כהן', true) }),
})
const second = group({ source_file: 'f1.pdf', commitment: commitment({ first_name: field('שרה', true) }) })
const finished = job([first, second])

function corrected(name: 'id_number', value: string) {
  const updated = structuredClone(finished)
  updated.groups[0].commitment[name] = field(value, false, { source: 'manual', raw_value: '000000019' })
  return updated
}

async function uploadOneFile() {
  const user = userEvent.setup()
  render(<App />)
  await user.upload(screen.getByLabelText('בחירת קבצים'), new File(['%PDF-'], 'טופס.pdf', { type: 'application/pdf' }))
  expect(screen.getByText('טופס.pdf')).toBeTruthy()
  await user.click(screen.getByRole('button', { name: 'התחלת עיבוד' }))
  await screen.findByRole('table')
  return user
}

beforeEach(() => {
  vi.mocked(api.getSenders).mockResolvedValue(['כללית'])
  vi.mocked(api.uploadFiles).mockResolvedValue(job([], { status: 'pending' }))
  vi.mocked(api.streamJob).mockImplementation(async (_id, handlers) => {
    handlers.onProgress({ status: 'running', forms_total: 2, forms_done: 1 })
    handlers.onRows([first])
    return finished
  })
  vi.mocked(api.getCrop).mockResolvedValue({ kind: 'missing', detail: api.NO_BOX })
  vi.mocked(api.getPage).mockResolvedValue({ kind: 'image', blob: new Blob() })
  vi.mocked(api.exportJob).mockResolvedValue(new Blob())
})

describe('App', () => {
  it('uploads, fills the table and counts the fields to review', async () => {
    await uploadOneFile()
    expect(api.uploadFiles).toHaveBeenCalledWith([expect.objectContaining({ name: 'טופס.pdf' })])
    expect(screen.getAllByRole('row')).toHaveLength(3)
    expect(screen.getByText('3 לבדיקה')).toBeTruthy()
  })

  it('a confirmed field stops being orange and the next orange field opens', async () => {
    vi.mocked(api.correctField).mockResolvedValue(corrected('id_number', '000000026'))
    const user = await uploadOneFile()
    await user.click(screen.getByRole('button', { name: 'ת.ז.: 000000019 (לבדיקה)' }))
    const input = screen.getByRole('textbox', { name: 'ערך' })
    await user.clear(input)
    await user.type(input, '000000026{Enter}')

    const cell = await screen.findByRole('button', { name: 'ת.ז.: 000000026' })
    expect(cell.className).not.toContain('review')
    expect(cell.className).toContain('manual')
    expect(screen.getByText('2 לבדיקה')).toBeTruthy()
    // The next orange field in reading order is the second form's first name.
    await waitFor(() => expect(screen.getByRole('complementary', { name: 'פרטי השדה שם פרטי' })).toBeTruthy())
  })

  it('Tab in the panel jumps to the next orange field', async () => {
    const user = await uploadOneFile()
    await user.click(screen.getByRole('button', { name: 'שם משפחה: כהן (לבדיקה)' }))
    await user.keyboard('{Tab}')
    expect(screen.getByRole('complementary', { name: 'פרטי השדה ת.ז.' })).toBeTruthy()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it('warns before exporting unconfirmed fields and leaves the choice to the reviewer', async () => {
    const user = await uploadOneFile()
    await user.click(screen.getByRole('button', { name: 'ייצוא לאקסל' }))
    const dialog = screen.getByRole('alertdialog')
    expect(within(dialog).getByText(/3 שדות עדיין מסומנים לבדיקה/)).toBeTruthy()
    await user.click(within(dialog).getByRole('button', { name: 'חזרה לבדיקה' }))
    expect(api.exportJob).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'ייצוא לאקסל' }))
    await user.click(screen.getByRole('button', { name: 'ייצוא בכל זאת' }))
    expect(api.exportJob).toHaveBeenCalledWith('j1')
  })

  it('exports without a warning once everything is confirmed', async () => {
    vi.mocked(api.streamJob).mockResolvedValue(job([group()]))
    const user = await uploadOneFile()
    await user.click(screen.getByRole('button', { name: 'ייצוא לאקסל' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(api.exportJob).toHaveBeenCalled()
  })

  it('shows a failed upload in Hebrew', async () => {
    vi.mocked(api.uploadFiles).mockRejectedValue(new api.ApiError(415, 'only PDF, PNG, JPEG, TIFF or WEBP files are supported'))
    const user = userEvent.setup()
    render(<App />)
    await user.upload(screen.getByLabelText('בחירת קבצים'), new File(['x'], 'a.pdf', { type: 'application/pdf' }))
    await user.click(screen.getByRole('button', { name: 'התחלת עיבוד' }))
    expect((await screen.findByRole('alert')).textContent).toBe('ההעלאה נכשלה: ניתן להעלות רק PDF, PNG, JPEG, TIFF או WEBP')
  })

  it('tells to split a file refused at upload for the memory budget', async () => {
    vi.mocked(api.uploadFiles).mockRejectedValue(new api.ApiError(413, 'file exceeds the memory budget'))
    const user = userEvent.setup()
    render(<App />)
    await user.upload(screen.getByLabelText('בחירת קבצים'), new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' }))
    await user.click(screen.getByRole('button', { name: 'התחלת עיבוד' }))
    expect((await screen.findByRole('alert')).textContent).toBe('ההעלאה נכשלה: הקובץ גדול מדי לשרת ההדגמה, נסו לפצל אותו')
  })

  it('tells to split a file whose job failed for the memory budget', async () => {
    vi.mocked(api.streamJob).mockResolvedValue(job([], { status: 'failed', error: 'PixelBudgetError' }))
    const user = userEvent.setup()
    render(<App />)
    await user.upload(screen.getByLabelText('בחירת קבצים'), new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' }))
    await user.click(screen.getByRole('button', { name: 'התחלת עיבוד' }))
    expect((await screen.findByRole('status')).textContent).toContain('(הקובץ גדול מדי לשרת ההדגמה, נסו לפצל אותו)')
  })
})
