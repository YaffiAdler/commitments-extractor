import { useState } from 'react'
import {
  type Commitment,
  type Field,
  type FormGroup,
  type Job,
  SPLIT_UNCERTAIN,
  deleteJob,
  exportJob,
  getJob,
  uploadFile,
} from './api'

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

function cell(value: Field | Field[]): { text: string; review: boolean } {
  if (Array.isArray(value)) {
    return {
      text: value.map((f) => f.value).filter(Boolean).join('; '),
      review: value.length === 0 || value.some((f) => f.needs_review),
    }
  }
  return { text: value.value ?? '', review: value.needs_review }
}

const SPLIT_UNCERTAIN_TEXT = 'ייתכן שפוצל לא נכון'

// Pages are 0-based in the API and 1-based for the reader.
const pageNumbers = (pages: number[]) => pages.map((p) => p + 1).join(', ')

const splitUncertain = (group: FormGroup) => group.split_reasons.includes(SPLIT_UNCERTAIN)

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function App() {
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleUpload(file: File) {
    setError(null)
    setBusy(true)
    try {
      if (job) await deleteJob(job.id)
      let current = await uploadFile(file)
      setJob(current)
      while (current.status === 'pending' || current.status === 'running') {
        await sleep(POLL_INTERVAL_MS)
        current = await getJob(current.id)
        setJob(current)
      }
    } catch (e) {
      setError(`ההעלאה נכשלה (${(e as Error).message})`)
    } finally {
      setBusy(false)
    }
  }

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
      </section>

      {error && <p className="error">{error}</p>}

      {job && job.groups.length > 0 && (
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
            {job.groups.map((group) => (
              <tr key={group.id}>
                {COLUMNS.map((c) => {
                  const { text, review } = cell(group.commitment[c.key] as Field | Field[])
                  return (
                    <td key={c.key} className={review ? 'review' : undefined}>
                      {text}
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
