import { type KeyboardEvent, useCallback, useEffect, useId, useRef, useState } from 'react'
import { type BBox, type FormGroup, IMAGE_GONE, type ImageResult, type Job, NO_BOX, correctField, getCrop, getPage } from '../api'
import { failureText } from '../messages'
import { type FieldTarget, columnOf, correctionValue, displayValue, entryAt, pageNumbers } from '../review'
import { Icon } from './Icon'

type Loaded = { state: 'loading' } | { state: 'image'; url: string } | { state: 'missing'; text: string } | { state: 'error'; text: string }

const MISSING_TEXT: Record<string, string> = {
  [IMAGE_GONE]: 'התמונה כבר לא זמינה (פג תוקף הקבצים הזמניים)',
  [NO_BOX]: 'למודל אין מיקום של השדה בעמוד',
}
const DEFAULT_MISSING_TEXT = 'אין תמונה לשדה הזה'
const KEY_HELP: [string, string][] = [
  ['Enter', 'אישור ושמירה'],
  ['Tab', 'השדה הכתום הבא'],
  ['Shift+Tab', 'השדה הכתום הקודם'],
  ['Esc', 'סגירה'],
]

// Loads an image (crop or page) with `load` (a stable callback); revokes the URL when it changes.
function useImage(load: () => Promise<ImageResult>): Loaded {
  // Tagged with the loader it came from, so a new loader reads as "loading" until it answers.
  const [result, setResult] = useState<{ load: typeof load; loaded: Loaded } | null>(null)
  useEffect(() => {
    let url: string | null = null
    let cancelled = false
    load()
      .then((answer) => {
        if (cancelled) return
        if (answer.kind === 'image') {
          url = URL.createObjectURL(answer.blob)
          setResult({ load, loaded: { state: 'image', url } })
        } else {
          const text = (answer.detail && MISSING_TEXT[answer.detail]) || DEFAULT_MISSING_TEXT
          setResult({ load, loaded: { state: 'missing', text } })
        }
      })
      .catch((e) => !cancelled && setResult({ load, loaded: { state: 'error', text: failureText('טעינת התמונה נכשלה', e) } }))
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [load])
  return result?.load === load ? result.loaded : { state: 'loading' }
}

function ImageState({ loaded }: { loaded: Exclude<Loaded, { state: 'image' }> }) {
  if (loaded.state === 'loading') return <p className="muted">טוען תמונה…</p>
  return <p className={loaded.state === 'error' ? 'error' : 'muted'}>{loaded.text}</p>
}

// The whole page with the field's box outlined; the box is in the page image's own pixels.
function PageView({ loaded, bbox, title }: { loaded: Loaded; bbox: BBox | null; title: string }) {
  const [size, setSize] = useState<{ width: number; height: number } | null>(null)
  if (loaded.state !== 'image') return <ImageState loaded={loaded} />
  const box = bbox && size && {
    left: `${(100 * bbox.x0) / size.width}%`,
    top: `${(100 * bbox.y0) / size.height}%`,
    width: `${(100 * (bbox.x1 - bbox.x0)) / size.width}%`,
    height: `${(100 * (bbox.y1 - bbox.y0)) / size.height}%`,
  }
  return (
    <div className="page-view" dir="ltr">
      <img
        src={loaded.url}
        alt={title}
        onLoad={(e) => setSize({ width: e.currentTarget.naturalWidth, height: e.currentTarget.naturalHeight })}
      />
      {box && <div className="highlight" style={box} aria-hidden="true" />}
    </div>
  )
}

interface Props {
  job: Job
  group: FormGroup
  target: FieldTarget
  senders: string[]
  onSaved: (job: Job, target: FieldTarget) => void
  // Move to the next (1) or previous (-1) field to review.
  onNavigate: (step: 1 | -1) => void
  onClose: () => void
}

// Render with key={targetKey(target)}: opening another field starts a fresh panel.
export function FieldPanel({ job, group, target, senders, onSaved, onNavigate, onClose }: Props) {
  const column = columnOf(target.field)
  const entry = entryAt(group, target)
  const field = entry?.field ?? null
  const editable = job.status === 'done'
  const listCount = target.item === null ? 0 : (group.commitment[target.field] as unknown[]).length
  const title = target.item !== null && listCount > 1 ? `${column.header} ${target.item + 1}` : column.header
  const page = field?.bbox?.page ?? group.pages[0]
  const hasBox = field?.bbox != null

  const { id: jobId } = job
  const { id: groupId } = group
  const { field: name, item } = target
  const loadCrop = useCallback(
    (): Promise<ImageResult> =>
      hasBox ? getCrop(jobId, groupId, name, item) : Promise.resolve({ kind: 'missing', detail: NO_BOX }),
    [jobId, groupId, name, item, hasBox],
  )
  const loadPage = useCallback(() => getPage(jobId, groupId, page), [jobId, groupId, page])
  const crop = useImage(loadCrop)
  const pageImage = useImage(loadPage)

  const [text, setText] = useState(() => displayValue(name, field?.value ?? null))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const inputId = useId()
  const listId = useId()

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [])

  async function save() {
    const value = correctionValue(target.field, text)
    if (value === null) {
      setError(target.field === 'received_date' ? 'תאריך בפורמט DD/MM/YYYY (שנה בארבע ספרות)' : 'הערך לא יכול להיות ריק')
      return
    }
    setSaving(true)
    setError(null)
    try {
      onSaved(await correctField(job.id, group.id, target.field, value, target.item), target)
    } catch (e) {
      setError(failureText('השמירה נכשלה', e))
    } finally {
      setSaving(false)
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault()
      if (!saving) void save()
    } else if (event.key === 'Tab') {
      event.preventDefault()
      onNavigate(event.shiftKey ? -1 : 1)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
    }
  }

  const status = !field?.value
    ? { text: 'חסר', className: 'review' }
    : field.source === 'manual'
      ? { text: 'תוקן ידנית', className: 'manual' }
      : entry?.review
        ? { text: 'לבדיקה', className: 'review' }
        : { text: 'תקין', className: 'ok' }
  const explanations = field?.explanations.length ? field.explanations : (field?.reasons ?? [])

  return (
    <aside className="field-panel" aria-label={`פרטי השדה ${title}`}>
      <header>
        <h2>
          {title} <span className={`status ${status.className}`}>{status.text}</span>
        </h2>
        <button type="button" className="icon-button" onClick={onClose} aria-label="סגירת החלונית">
          <Icon name="close" />
        </button>
      </header>

      <p className="muted">
        עמודים {pageNumbers(group.pages)}
        {field?.source === 'manual' && ' · הערך הוזן ידנית'}
      </p>

      <div className="edit">
        <label htmlFor={inputId}>ערך</label>
        <input
          id={inputId}
          ref={inputRef}
          value={text}
          dir={column.ltr ? 'ltr' : undefined}
          disabled={!editable || saving}
          list={target.field === 'sender' ? listId : undefined}
          placeholder={target.field === 'received_date' ? 'DD/MM/YYYY' : undefined}
          aria-invalid={error !== null}
          aria-describedby={`${inputId}-help`}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
        {target.field === 'sender' && (
          <datalist id={listId}>
            {senders.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        )}
        {editable ? (
          <ul id={`${inputId}-help`} className="keys muted small">
            {KEY_HELP.map(([key, text]) => (
              <li key={key}>
                <kbd dir="ltr">{key}</kbd> {text}
              </li>
            ))}
          </ul>
        ) : (
          <p id={`${inputId}-help`} className="muted small">
            אפשר לתקן רק אחרי שהעיבוד הסתיים
          </p>
        )}
        {saving && <p className="muted">שומר…</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>

      <section>
        <h3>כפי שנקרא מהטופס</h3>
        {field?.raw_value ? (
          <p className="raw-value">
            <bdi dir={column.ltr ? 'ltr' : undefined}>{field.raw_value}</bdi>
          </p>
        ) : (
          <p className="muted">לא נקרא ערך</p>
        )}
      </section>

      <section>
        <h3>סיבות</h3>
        {explanations.length > 0 ? (
          <ul className="reasons">
            {explanations.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">אין</p>
        )}
      </section>

      <section>
        <h3>חיתוך השדה</h3>
        {crop.state === 'image' ? (
          <img className="crop" src={crop.url} alt={`חיתוך של ${title}`} />
        ) : (
          <ImageState loaded={crop} />
        )}
      </section>

      <section>
        <h3>עמוד {page + 1}</h3>
        <PageView loaded={pageImage} bbox={field?.bbox ?? null} title={`עמוד ${page + 1} של הטופס`} />
      </section>
    </aside>
  )
}
