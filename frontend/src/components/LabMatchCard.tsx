import { useState } from 'react'
import { type FormGroup, type LabMatch, getLabMatch } from '../api'
import { failureText } from '../messages'
import { displayValue } from '../review'
import { Icon } from './Icon'

export const LAB_DEMO_LABEL = 'מצב הדגמה: נתונים מדומים'
// Each group lists this many items; the counter shows the full number.
const MAX_LISTED = 5

interface Props {
  jobId: string
  // The job's forms in table order, to name a commitment "טופס N".
  groups: FormGroup[]
  disabled: boolean
}

const shownDate = (value: string | null) => displayValue('received_date', value)

function ShortList({ items }: { items: string[] }) {
  if (items.length === 0) return <p className="muted small">אין</p>
  const rest = items.length - MAX_LISTED
  return (
    <ul className="lab-list">
      {items.slice(0, MAX_LISTED).map((text, index) => (
        <li key={index}>{text}</li>
      ))}
      {rest > 0 && <li className="muted">ועוד {rest}</li>}
    </ul>
  )
}

export function LabMatchCard({ jobId, groups, disabled }: Props) {
  const [result, setResult] = useState<LabMatch | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const formName = (id: string) => `טופס ${groups.findIndex((g) => g.id === id) + 1}`

  async function check() {
    setError(null)
    setBusy(true)
    try {
      setResult(await getLabMatch(jobId))
    } catch (e) {
      setError(failureText('הבדיקה מול מערכת המעבדה נכשלה', e))
    } finally {
      setBusy(false)
    }
  }

  const groupsShown = result && [
    {
      title: 'מותאמות',
      attention: false,
      items: result.matched.map(
        (m) =>
          `${formName(m.commitment_id)} · מרכזי ${m.central_number}` +
          (m.date_differs ? ` · תאריך שונה: בטופס ${shownDate(m.commitment_received_date)}, במעבדה ${shownDate(m.sample.received_date)}` : ''),
      ),
    },
    {
      title: 'דגימות בלי התחייבות',
      attention: true,
      items: result.samples_without_commitment.map(
        (s) => `מרכזי ${s.central_number} · ${shownDate(s.received_date)} · ${s.sender}`,
      ),
    },
    {
      title: 'התחייבויות בלי דגימה',
      attention: true,
      items: result.commitments_without_sample.map(
        (c) => `${formName(c.commitment_id)} · ${c.central_number ? `מרכזי ${c.central_number}` : 'לא נקרא מספר מרכזי'}`,
      ),
    },
  ]

  return (
    <>
      <div className="toolbar">
        <button type="button" onClick={() => void check()} disabled={disabled || busy}>
          <Icon name="check" />
          בדיקה מול מערכת המעבדה
        </button>
      </div>
      {error && (
        <p className="error banner" role="alert">
          {error}
        </p>
      )}
      {groupsShown && (
        <section className="card lab-match" aria-label="בדיקה מול מערכת המעבדה">
          <div className="card-header">
            <h3 className="card-title">בדיקה מול מערכת המעבדה</h3>
            <span className="lab-demo-label">{LAB_DEMO_LABEL}</span>
          </div>
          <dl className="stats">
            {groupsShown.map((g) => (
              <div key={g.title} className={g.attention && g.items.length > 0 ? 'attention' : undefined}>
                <dt>{g.title}</dt>
                <dd>{g.items.length}</dd>
              </div>
            ))}
          </dl>
          <div className="lab-groups">
            {groupsShown.map((g) => (
              <section key={g.title} aria-label={g.title}>
                <h4>{g.title}</h4>
                <ShortList items={g.items} />
              </section>
            ))}
          </div>
        </section>
      )}
    </>
  )
}
