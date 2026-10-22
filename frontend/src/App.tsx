import { useCallback, useEffect, useMemo, useState } from 'react'
import { type FormGroup, type Job, type JobProgress, deleteJob, exportJob, getJob, getSenders, streamJob, uploadFiles } from './api'
import { ExportButton } from './components/ExportButton'
import { FieldPanel } from './components/FieldPanel'
import { Icon } from './components/Icon'
import { LabMatchCard } from './components/LabMatchCard'
import { ReviewTable } from './components/ReviewTable'
import { UploadPanel } from './components/UploadPanel'
import { failureText } from './messages'
import {
  type FieldTarget,
  formFlags,
  mergeRows,
  nextReviewTarget,
  pageNumbers,
  progressOf,
  reviewTargets,
  sortGroups,
  targetKey,
} from './review'

// Polling is the fallback when the event stream is unavailable.
const POLL_INTERVAL_MS = 1000
const EXPORT_FILE_NAME = 'commitments.xlsx'
// The browser must start the download before the blob URL is revoked.
const REVOKE_DELAY_MS = 1000

const STATUS_TEXT: Record<Job['status'], string> = {
  pending: 'ממתין לעיבוד',
  running: 'מעבד',
  done: 'העיבוד הסתיים',
  failed: 'העיבוד נכשל',
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const isActive = (job: Job) => job.status === 'pending' || job.status === 'running'

function focusCell(target: FieldTarget) {
  // After React has rendered the table with the new state.
  requestAnimationFrame(() => document.querySelector<HTMLElement>(`button[data-target="${targetKey(target)}"]`)?.focus())
}

function App() {
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<JobProgress | null>(null)
  const [selected, setSelected] = useState<FieldTarget | null>(null)
  const [onlyUncertain, setOnlyUncertain] = useState(false)
  const [senders, setSenders] = useState<string[]>([])

  useEffect(() => {
    // Without the list the sender field is still editable; the backend checks the name.
    getSenders().then(setSenders, () => setSenders([]))
  }, [])

  // Only forms whose reading finished (polling also returns the unfinished ones), in file and page order.
  const rows = useMemo(() => (job ? sortGroups(job.groups.filter((g) => g.finished), job.files) : []), [job])
  const fileIndex = useCallback((group: FormGroup) => job?.files.indexOf(group.source_file) ?? 0, [job])
  const selectedGroup = selected ? rows.find((g) => g.id === selected.groupId) : undefined

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
        onRows: (newRows) => setJob((current) => current && { ...current, groups: mergeRows(current.groups, newRows) }),
      })
      setJob(finished)
      setProgress(progressOf(finished))
    } catch {
      // Not an error to show: polling reports a real failure (HTTP status) itself.
      await pollJob(jobId)
    }
  }

  async function handleUpload(files: File[]) {
    setError(null)
    setBusy(true)
    setProgress(null)
    setSelected(null)
    try {
      // One job at a time on the screen; the previous one is deleted from the server.
      if (job) await deleteJob(job.id).catch(() => undefined)
      setJob(null)
      const created = await uploadFiles(files)
      setJob(created)
      setProgress(progressOf(created))
      if (isActive(created)) await followJob(created.id)
    } catch (e) {
      setError(failureText('ההעלאה נכשלה', e))
    } finally {
      setBusy(false)
    }
  }

  async function handleExport() {
    if (!job) return
    setError(null)
    try {
      const url = URL.createObjectURL(await exportJob(job.id))
      const link = document.createElement('a')
      link.href = url
      link.download = EXPORT_FILE_NAME
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS)
    } catch (e) {
      setError(failureText('הייצוא נכשל', e))
    }
  }

  async function handleDelete() {
    if (!job) return
    setError(null)
    try {
      await deleteJob(job.id)
      setJob(null)
      setProgress(null)
      setSelected(null)
    } catch (e) {
      setError(failureText('המחיקה נכשלה', e))
    }
  }

  const select = useCallback((target: FieldTarget) => setSelected(target), [])

  function navigate(step: 1 | -1) {
    const next = nextReviewTarget(rows, selected, step)
    if (next) setSelected(next)
  }

  function handleSaved(updated: Job, target: FieldTarget) {
    setJob(updated)
    const updatedRows = sortGroups(updated.groups.filter((g) => g.finished), updated.files)
    // After a confirmation the next orange field opens, so the reviewer can keep typing.
    const next = nextReviewTarget(updatedRows, target, 1)
    setSelected(next ?? target)
  }

  function closePanel() {
    if (selected) focusCell(selected)
    setSelected(null)
  }

  const unconfirmed = reviewTargets(rows).length
  const flaggedForms = rows.filter((g) => formFlags(g).length > 0).length

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <Icon name="file" size={18} />
          </span>
          <span className="brand-name">חילוץ התחייבויות</span>
        </div>
        <nav className="breadcrumbs" aria-label="מיקום">
          אגף דיאגנוסטיקה <span aria-hidden="true">›</span> המעבדה המרכזית לנגיפים <span aria-hidden="true">›</span>{' '}
          <span className="current">טפסי התחייבות</span>
        </nav>
      </header>

      <section className="hero">
        <HeroLines />
        <div className="hero-text">
          <h1>חילוץ התחייבויות</h1>
          <p>טפסי התחייבות שהתקבלו במעבדה המרכזית לנגיפים · קריאה, בדיקה וייצוא לאקסל</p>
        </div>
      </section>

      <div className={`layout${selectedGroup ? ' with-panel' : ''}`}>
      <main>
        <header className="page-header">
          {job ? (
            <dl className="stats" aria-label="סיכום העבודה">
              <div>
                <dt>טפסים</dt>
                <dd>{rows.length}</dd>
              </div>
              <div className={unconfirmed > 0 ? 'attention' : undefined}>
                <dt>שדות לבדיקה</dt>
                <dd>{unconfirmed}</dd>
              </div>
              <div className={flaggedForms > 0 ? 'flagged' : undefined}>
                <dt>טפסים עם דגל</dt>
                <dd>{flaggedForms}</dd>
              </div>
            </dl>
          ) : (
            <p className="muted">העלו קבצים, עברו על השדות המסומנים וייצאו לאקסל</p>
          )}
          {job && (
            <section className="job-bar" aria-label="פעולות על העבודה">
              <span role="status" className={`job-status ${job.status}`}>
                {STATUS_TEXT[job.status]}
                {job.status === 'failed' && job.error && ` (${job.error})`}
              </span>
              <button type="button" className="ghost" onClick={() => void handleDelete()} disabled={busy && isActive(job)}>
                <Icon name="trash" />
                מחיקת העבודה מהשרת
              </button>
              <ExportButton
                disabled={job.status !== 'done'}
                unconfirmedFields={unconfirmed}
                flaggedForms={flaggedForms}
                onExport={() => void handleExport()}
              />
            </section>
          )}
        </header>

        <UploadPanel busy={busy} progress={job && isActive(job) ? progress : null} onUpload={(f) => void handleUpload(f)} />

        {error && (
          <p className="error banner" role="alert">
            {error}
          </p>
        )}

        {rows.length > 0 && (
          <ReviewTable
            groups={rows}
            fileIndex={fileIndex}
            multipleFiles={(job?.files.length ?? 0) > 1}
            selected={selected}
            onSelect={select}
            onlyUncertain={onlyUncertain}
            onOnlyUncertainChange={setOnlyUncertain}
          />
        )}

        {job && rows.length > 0 && (
          <LabMatchCard key={job.id} jobId={job.id} groups={rows} disabled={job.status !== 'done'} />
        )}

        {job && job.irrelevant_pages.length > 0 && (
          <p className="muted small">עמודים שלא שויכו לאף טופס: {pageNumbers(job.irrelevant_pages.map((p) => p.page))}</p>
        )}

        <ul className="legend" aria-label="מקרא">
          <li>
            <span className="review swatch" /> שדה שדורש בדיקה ידנית
          </li>
          <li>
            <span className="manual swatch" /> תוקן ידנית
          </li>
        </ul>
      </main>

      {job && selected && selectedGroup && (
        <FieldPanel
          key={targetKey(selected)}
          job={job}
          group={selectedGroup}
          target={selected}
          senders={senders}
          onSaved={handleSaved}
          onNavigate={navigate}
          onClose={closePanel}
        />
      )}
      </div>
    </div>
  )
}

// Concentric arcs (teal, pink, sand) at the far side of the title, centred below the strip.
const HERO_ARCS: [radius: number, color: string][] = [
  [250, 'var(--teal)'],
  [205, 'var(--pink-light)'],
  [160, 'var(--sand)'],
  [115, 'var(--teal-light)'],
]

function HeroLines() {
  return (
    <svg className="hero-lines" viewBox="0 0 520 260" aria-hidden="true">
      {HERO_ARCS.map(([radius, color]) => (
        <circle key={radius} cx="260" cy="300" r={radius} stroke={color} />
      ))}
    </svg>
  )
}

export default App
