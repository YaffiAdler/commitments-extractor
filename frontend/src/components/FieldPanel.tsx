import { type KeyboardEvent, useCallback, useEffect, useId, useRef, useState } from 'react'
import {
  type BBox,
  type FormGroup,
  IMAGE_GONE,
  type ImageResult,
  type Job,
  NO_BOX,
  type ReviewLevelConfig,
  correctField,
  getCrop,
  getPage,
} from '../api'
import { failureText } from '../messages'
import {
  type FieldTarget,
  REVIEW_LEVELS,
  REVIEW_LEVEL_ORDER,
  type ReviewLevel,
  columnOf,
  confidencePercent,
  correctionValue,
  displayValue,
  entryAt,
  isNameField,
  levelCounts,
  nameCandidates,
  pageNumbers,
  reviewLevel,
} from '../review'
import { Icon, type IconName } from './Icon'

type Loaded = { state: 'loading' } | { state: 'image'; url: string } | { state: 'missing'; text: string } | { state: 'error'; text: string }

const MISSING_TEXT: Record<string, string> = {
  [IMAGE_GONE]: 'התמונה כבר לא זמינה (פג תוקף הקבצים הזמניים)',
  [NO_BOX]: 'למודל אין מיקום של השדה בעמוד',
}
const DEFAULT_MISSING_TEXT = 'אין תמונה לשדה הזה'
// The text carries the level; the icon repeats it, so it never rests on colour alone.
const LEVEL_ICONS: Record<ReviewLevel, IconName> = { glance: 'eye', careful: 'search', verify: 'alert' }
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

function LevelBadge({ level }: { level: ReviewLevel }) {
  return (
    <span className={`level-badge ${level}`}>
      <Icon name={LEVEL_ICONS[level]} size={16} />
      {REVIEW_LEVELS[level].label}
    </span>
  )
}

interface Props {
  job: Job
  group: FormGroup
  target: FieldTarget
  senders: string[]
  // Null while the thresholds are not loaded (or failed): no level is shown.
  reviewLevels: ReviewLevelConfig | null
  onSaved: (job: Job, target: FieldTarget) => void
  // Move to the next (1) or previous (-1) field to review.
  onNavigate: (step: 1 | -1) => void
  onClose: () => void
}

// Render with key={targetKey(target)}: opening another field starts a fresh panel.
export function FieldPanel({ job, group, target, senders, reviewLevels, onSaved, onNavigate, onClose }: Props) {
  const column = columnOf(target.field)
  const entry = entryAt(group, target)
  const field = entry?.field ?? null
  const editable = job.status === 'done'
  const listCount = target.item === null ? 0 : (group.commitment[target.field] as unknown[]).length
  const title = target.item !== null && listCount > 1 ? `${column.header} ${target.item + 1}` : column.header
  const page = field?.bbox?.page ?? group.pages[0]
  const hasBox = field?.bbox != null
  const level = reviewLevels && entry ? reviewLevel(entry, reviewLevels) : null
  const percent = reviewLevels ? confidencePercent(field, reviewLevels) : null
  const counts = reviewLevels ? levelCounts(group, reviewLevels) : null

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

  async function save(input = text) {
    const value = correctionValue(target.field, input)
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
  const nameField = isNameField(target.field)
  const candidates = nameField && editable ? nameCandidates(field) : []

  function choose(candidate: string) {
    setText(candidate)
    void save(candidate)
  }

  // Names: the crop sits right after the input, so the reviewer reads the handwriting beside the value.
  const cropSection = (
    <section>
      <h3>חיתוך השדה</h3>
      {crop.state === 'image' ? (
        <img className="crop" src={crop.url} alt={`חיתוך של ${title}`} />
      ) : (
        <ImageState loaded={crop} />
      )}
    </section>
  )
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

      {counts && (
        <div className="level-counts">
          <p className="muted small">שדות בטופס לפי רמת בדיקה</p>
          <ul aria-label="שדות בטופס לפי רמת בדיקה">
            {REVIEW_LEVEL_ORDER.map((key) => (
              <li key={key}>
                <LevelBadge level={key} /> <span className="count">{counts[key]}</span>
              </li>
            ))}
            <li>
              <span className="level-badge manual">
                <Icon name="check" size={16} />
                תוקנו ידנית
              </span>{' '}
              <span className="count">{counts.manual}</span>
            </li>
          </ul>
        </div>
      )}

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
        {candidates.length > 0 && (
          <div className="name-candidates">
            <p className="muted small">קריאות אחרות של השם (לחיצה שומרת)</p>
            <ul aria-label="קריאות אחרות של השם">
              {candidates.map((candidate) => (
                <li key={candidate}>
                  <button type="button" disabled={saving} onClick={() => choose(candidate)}>
                    <bdi>{candidate}</bdi>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {saving && <p className="muted">שומר…</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>

      {nameField && cropSection}

      <section className="review-level" aria-label="רמת בדיקה לפני אישור">
        <h3>רמת בדיקה לפני אישור</h3>
        {level ? (
          <>
            <p>
              <LevelBadge level={level} />
            </p>
            <p className="muted small">{REVIEW_LEVELS[level].hint}</p>
          </>
        ) : field?.source === 'manual' ? (
          <p className="muted small">הערך הוזן ידנית ואין בו עוד מה לבדוק</p>
        ) : (
          <p className="muted small">רמת הבדיקה אינה זמינה כרגע</p>
        )}
        {percent !== null && (
          <p className="muted small">
            ציון ביטחון (לא מכויל, אינו סיכוי לדיוק): <bdi dir="ltr">{percent}%</bdi>
          </p>
        )}
        <h4>סיבות</h4>
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
        <h3>כפי שנקרא מהטופס</h3>
        {field?.raw_value ? (
          <p className="raw-value">
            <bdi dir={column.ltr ? 'ltr' : undefined}>{field.raw_value}</bdi>
          </p>
        ) : (
          <p className="muted">לא נקרא ערך</p>
        )}
      </section>

      {!nameField && cropSection}

      <section>
        <h3>עמוד {page + 1}</h3>
        <PageView loaded={pageImage} bbox={field?.bbox ?? null} title={`עמוד ${page + 1} של הטופס`} />
      </section>
    </aside>
  )
}
