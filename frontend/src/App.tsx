import { useState } from 'react'
import {
  type Commitment,
  type Field,
  type FormGroup,
  type Job,
  type JobProgress,
  SPLIT_UNCERTAIN,
  deleteJob,
  exportJob,
  getCrop,
  getJob,
  streamJob,
  uploadFile,
} from './api'

// Polling is the fallback when the event stream is unavailable.
const POLL_INTERVAL_MS = 1000
const EXPORT_FILE_NAME = 'commitments.xlsx'

const COLUMNS: { header: string; key: keyof Commitment }[] = [
  { header: 'שם פרטי', key: 'first_name' },
  { header: 'שם משפחה', key: 'last_name' },
  { header: 'ת.ז.', key: 'id_number' },
  { header: 'מספר מרכזי', key: 'central_numbers' },
  { header: 'תאריך קבלה', key: 'received_date' },
  { header: 'גורם שולח', key: 'sender' },
  { header: 'מספר התחייבות', key: 'commitment_numbers' },
]

const STATUS_TEXT: Record<Job['status'], string> = {
  pending: 'ממתין…',
  running: 'מעבד…',
  done: 'הסתיים',
  failed: 'העיבוד נכשל',
}

// Mirrors Commitment.field_needs_review: an empty list field is flagged unless optional.
function cell(value: Field | Field[], optional: boolean): { text: string; review: boolean } {
  if (Array.isArray(value)) {
    return {
      text: value.map((f) => f.value).filter(Boolean).join('; '),
      review: (value.length === 0 && !optional) || value.some((f) => f.needs_review),
    }
  }
  return { text: value.value ?? '', review: value.needs_review }
}

const SPLIT_UNCERTAIN_TEXT = 'ייתכן שפוצל לא נכון'
const IMAGE_GONE_TEXT = 'התמונה כבר לא זמינה'

// Values of a cell that have a box on the page; item is the list index (null for a single field).
function boxedItems(value: Field | Field[]): (number | null)[] {
  if (Array.isArray(value)) return value.flatMap((f, i) => (f.bbox ? [i] : []))
  return value.bbox ? [null] : []
}

type Preview = { title: string; url: string | null }

// Pages are 0-based in the API and 1-based for the reader.
const pageNumbers = (pages: number[]) => pages.map((p) => p + 1).join(', ')

const splitUncertain = (group: FormGroup) => group.split_reasons.includes(SPLIT_UNCERTAIN)

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const isActive = (job: Job) => job.status === 'pending' || job.status === 'running'

// Same numbers as the 'progress' event, for the polling fallback.
const progressOf = (job: Job): JobProgress => ({
  status: job.status,
  forms_total: job.groups.length,
  forms_done: job.groups.filter((g) => g.finished).length,
})

// Rows arrive as forms finish, in any order; keep them in page order.
function mergeRows(groups: FormGroup[], rows: FormGroup[]): FormGroup[] {
  const ids = new Set(rows.map((r) => r.id))
  return [...groups.filter((g) => !ids.has(g.id)), ...rows].sort((a, b) => a.pages[0] - b.pages[0])
}

function App() {
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<JobProgress | null>(null)
  // url null: the page image is gone (file TTL or server restart).
  const [preview, setPreview] = useState<Preview | null>(null)

  function showPreview(next: Preview | null) {
    setPreview((current) => {
      if (current?.url) URL.revokeObjectURL(current.url)
      return next
    })
  }

  async function handleCrop(group: FormGroup, key: string, header: string, item: number | null) {
    if (!job) return
    const title = item === null ? header : `${header} ${item + 1}`
    try {
      const crop = await getCrop(job.id, group.id, key, item)
      showPreview({ title, url: crop.kind === 'image' ? URL.createObjectURL(crop.blob) : null })
    } catch (e) {
      setError(`טעינת התמונה נכשלה (${(e as Error).message})`)
    }
  }

  async function pollJob(jobId: string) {
    let current = await getJob(jobId)
    setJob(current)
    setProgress(progressOf(current))
    while (isActive(current)) {
      await sleep(POLL_INTERVAL_MS)
      current = await getJob(jobId)
      setJob(current)
      setProgress(progressOf(current))
    }
  }

  async function followJob(jobId: string) {
    try {
      const finished = await streamJob(jobId, {
        onProgress: (p) => {
          setProgress(p)
          setJob((current) => current && { ...current, status: p.status })
        },
        onRows: (rows) => setJob((current) => current && { ...current, groups: mergeRows(current.groups, rows) }),
      })
      setJob(finished)
      setProgress(progressOf(finished))
    } catch {
      // Not an error to show: polling reports a real failure (HTTP status) itself.
      await pollJob(jobId)
    }
  }

  async function handleUpload(file: File) {
    setError(null)
    setBusy(true)
    setProgress(null)
    showPreview(null)
    try {
      if (job) await deleteJob(job.id)
      const created = await uploadFile(file)
      setJob(created)
      if (isActive(created)) await followJob(created.id)
    } catch (e) {
      setError(`ההעלאה נכשלה (${(e as Error).message})`)
    } finally {
      setBusy(false)
    }
  }

  // Only forms whose reading finished (polling also returns the unfinished ones).
  const rows = job?.groups.filter((g) => g.finished) ?? []

  async function handleExport() {
    if (!job) return
    try {
      const url = URL.createObjectURL(await exportJob(job.id))
      const link = document.createElement('a')
      link.href = url
      link.download = EXPORT_FILE_NAME
      link.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(`הייצוא נכשל (${(e as Error).message})`)
    }
  }

  return (
    <main>
      <h1>חילוץ התחייבויות</h1>

      <section className="toolbar">
        <label className="button">
          העלאת קובץ (PDF או תמונה)
          <input
            type="file"
            accept="application/pdf,image/png,image/jpeg,image/tiff,image/webp"
            disabled={busy}
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (file) void handleUpload(file)
            }}
          />
        </label>
        <button type="button" onClick={handleExport} disabled={job?.status !== 'done'}>
          ייצוא לאקסל
        </button>
        {job && <span>{STATUS_TEXT[job.status]}</span>}
        {job && isActive(job) && progress && progress.forms_total > 0 && (
          <span>
            {progress.forms_done}/{progress.forms_total} טפסים
          </span>
        )}
      </section>

      {error && <p className="error">{error}</p>}

      {rows.length > 0 && (
        <table>
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key}>{c.header}</th>
              ))}
              <th>עמודים</th>
              <th>פיצול</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((group) => (
              <tr key={group.id}>
                {COLUMNS.map((c) => {
                  const value = group.commitment[c.key] as Field | Field[]
                  const { text, review } = cell(value, group.commitment.optional_fields.includes(c.key))
                  return (
                    <td key={c.key} className={review ? 'review' : undefined}>
                      {text}
                      {boxedItems(value).map((item) => (
                        <button
                          type="button"
                          className="crop-link"
                          key={item ?? 'single'}
                          onClick={() => void handleCrop(group, c.key, c.header, item)}
                        >
                          {item === null ? 'תמונה' : `תמונה ${item + 1}`}
                        </button>
                      ))}
                    </td>
                  )
                })}
                <td>{pageNumbers(group.pages)}</td>
                <td className={splitUncertain(group) ? 'review' : undefined}>
                  {splitUncertain(group) ? SPLIT_UNCERTAIN_TEXT : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {preview && (
        <section className="preview">
          <strong>{preview.title}</strong>
          {preview.url ? <img src={preview.url} alt={preview.title} /> : <p className="review-note">{IMAGE_GONE_TEXT}</p>}
          <button type="button" onClick={() => showPreview(null)}>
            סגירה
          </button>
        </section>
      )}

      {job && job.irrelevant_pages.length > 0 && (
        <p className="review-note">
          עמודים שלא שויכו לאף טופס: {pageNumbers(job.irrelevant_pages.map((p) => p.page))}
        </p>
      )}

      <p className="legend">
        <span className="review swatch" /> שדה שדורש בדיקה ידנית
      </p>
    </main>
  )
}

export default App
