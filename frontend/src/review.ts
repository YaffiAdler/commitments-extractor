// What the review screen derives from a job: rows, cells, the fields still
// to review and the order Tab walks them. Pure functions, no I/O.
import {
  type Field,
  type FieldName,
  type FormGroup,
  type Job,
  type JobProgress,
  type ListFieldName,
  LIST_FIELDS,
} from './api'

export interface Column {
  key: FieldName
  header: string
  // Digits and dates: shown left-to-right inside the right-to-left table.
  ltr: boolean
}

export const COLUMNS: Column[] = [
  { key: 'first_name', header: 'שם פרטי', ltr: false },
  { key: 'last_name', header: 'שם משפחה', ltr: false },
  { key: 'id_number', header: 'ת.ז.', ltr: true },
  { key: 'central_numbers', header: 'מספר מרכזי', ltr: true },
  { key: 'received_date', header: 'תאריך קבלה', ltr: true },
  { key: 'sender', header: 'גורם שולח', ltr: false },
  { key: 'commitment_numbers', header: 'מספר התחייבות', ltr: true },
]

export const columnOf = (key: FieldName): Column => COLUMNS.find((c) => c.key === key)!

// One field of one form; item is the list index for central / commitment numbers (null otherwise).
export interface FieldTarget {
  groupId: string
  field: FieldName
  item: number | null
}

export const sameTarget = (a: FieldTarget | null, b: FieldTarget | null) =>
  !!a && !!b && a.groupId === b.groupId && a.field === b.field && a.item === b.item

export const targetKey = (t: FieldTarget) => `${t.groupId}/${t.field}/${t.item ?? '-'}`

// One value in a cell. field is null for the slot of an empty list (editing it adds item 0).
export interface CellEntry {
  target: FieldTarget
  field: Field | null
  review: boolean
}

export const isListField = (name: FieldName): name is ListFieldName =>
  (LIST_FIELDS as readonly string[]).includes(name)

// The entries of one cell; mirrors Commitment.field_needs_review (an empty list is flagged unless optional).
export function cellEntries(group: FormGroup, name: FieldName): CellEntry[] {
  const { commitment } = group
  if (isListField(name)) {
    const items = commitment[name]
    if (items.length === 0) {
      const review = !commitment.optional_fields.includes(name)
      return [{ target: { groupId: group.id, field: name, item: 0 }, field: null, review }]
    }
    return items.map((field, item) => ({ target: { groupId: group.id, field: name, item }, field, review: field.needs_review }))
  }
  const field = commitment[name]
  return [{ target: { groupId: group.id, field: name, item: null }, field, review: field.needs_review }]
}

export function entryAt(group: FormGroup, target: FieldTarget): CellEntry | null {
  return cellEntries(group, target.field).find((e) => sameTarget(e.target, target)) ?? null
}

// Every entry of the given rows, in reading order (row by row, column by column).
export const allEntries = (groups: FormGroup[]): CellEntry[] =>
  groups.flatMap((group) => COLUMNS.flatMap((column) => cellEntries(group, column.key)))

// The fields still to review, in reading order: the "N לבדיקה" count and the export warning.
export const reviewTargets = (groups: FormGroup[]): FieldTarget[] =>
  allEntries(groups).filter((e) => e.review).map((e) => e.target)

// The next (step 1) or previous (step -1) field to review after current, wrapping
// around; current itself may already be confirmed. Null if nothing is left to review.
export function nextReviewTarget(groups: FormGroup[], current: FieldTarget | null, step: 1 | -1 = 1): FieldTarget | null {
  const entries = allEntries(groups)
  const found = entries.findIndex((e) => sameTarget(e.target, current))
  // Without a current entry, start just before the first (forward) or after the last (backward).
  const start = found !== -1 ? found : step === 1 ? -1 : entries.length
  for (let offset = 1; offset <= entries.length; offset++) {
    const index = (((start + step * offset) % entries.length) + entries.length) % entries.length
    const entry = entries[index]
    if (entry.review && !sameTarget(entry.target, current)) return entry.target
  }
  return null
}

// --- Form-level flags (mirror backend reason_texts.FORM_REASON_TEXTS) ------------

export const SPLIT_UNCERTAIN = 'split_uncertain'

export interface FormFlag {
  code: string
  label: string
  explanation: string
}

const FORM_FLAGS: FormFlag[] = [
  { code: SPLIT_UNCERTAIN, label: 'פיצול לא בטוח', explanation: 'ייתכן שפוצל לא נכון (גבולות הטופס בין העמודים אינם ודאיים)' },
  { code: 'form_type_unknown', label: 'סוג טופס לא מזוהה', explanation: 'סוג הטופס לא זוהה; נקרא בהנחיות כלליות' },
  { code: 'extraction_failed', label: 'הקריאה נכשלה', explanation: 'קריאת הטופס נכשלה' },
  { code: 'central_duplicate', label: 'מרכזי כפול', explanation: 'אותו מספר מרכזי מופיע גם בטופס אחר בעבודה זו' },
]

export const formFlags = (group: FormGroup): FormFlag[] =>
  FORM_FLAGS.filter((flag) => group.split_reasons.includes(flag.code) || group.form_reasons.includes(flag.code))

// Mirrors FormGroup.needs_review.
export const groupNeedsReview = (group: FormGroup): boolean =>
  formFlags(group).length > 0 || COLUMNS.some((c) => cellEntries(group, c.key).some((e) => e.review))

// --- Rows -------------------------------------------------------------------------

// Forms in file order (as uploaded), then page order.
export function sortGroups(groups: FormGroup[], files: string[]): FormGroup[] {
  const fileIndex = (g: FormGroup) => files.indexOf(g.source_file)
  return [...groups].sort((a, b) => fileIndex(a) - fileIndex(b) || a.pages[0] - b.pages[0])
}

// Rows arrive as forms finish, in any order; a row sent again replaces the old one.
export function mergeRows(groups: FormGroup[], rows: FormGroup[]): FormGroup[] {
  const ids = new Set(rows.map((r) => r.id))
  return [...groups.filter((g) => !ids.has(g.id)), ...rows]
}

// Same numbers as the 'progress' event, for the polling fallback.
export const progressOf = (job: Job): JobProgress => ({
  status: job.status,
  forms_total: job.groups.length,
  forms_done: job.groups.filter((g) => g.finished).length,
})

// Pages are 0-based in the API and 1-based for the reader.
export const pageNumbers = (pages: number[]) => pages.map((p) => p + 1).join(', ')

// --- Values -----------------------------------------------------------------------

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/
const DAY_FIRST_DATE = /^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$/

// A date as shown: DD/MM/YYYY (the API sends YYYY-MM-DD).
export function displayValue(name: FieldName, value: string | null): string {
  if (value === null) return ''
  const iso = name === 'received_date' ? ISO_DATE.exec(value) : null
  return iso ? `${iso[3]}/${iso[2]}/${iso[1]}` : value
}

function validDate(year: number, month: number, day: number): boolean {
  const date = new Date(Date.UTC(year, month - 1, day))
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
}

// The reviewer's date as YYYY-MM-DD, from DD/MM/YYYY (also . or -) or YYYY-MM-DD.
// A two-digit year is refused: the century is never guessed.
export function toIsoDate(text: string): string | null {
  const trimmed = text.trim()
  const iso = ISO_DATE.exec(trimmed)
  const dayFirst = DAY_FIRST_DATE.exec(trimmed)
  const [year, month, day] = iso
    ? [Number(iso[1]), Number(iso[2]), Number(iso[3])]
    : dayFirst
      ? [Number(dayFirst[3]), Number(dayFirst[2]), Number(dayFirst[1])]
      : [0, 0, 0]
  if (!validDate(year, month, day) || year === 0) return null
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

// The text the API expects for the field, or null if the reviewer's text is not valid.
export function correctionValue(name: FieldName, text: string): string | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  return name === 'received_date' ? toIsoDate(trimmed) : trimmed
}
