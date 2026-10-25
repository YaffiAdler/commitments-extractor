// Hebrew messages for failed requests. The backend's `detail` is a fixed code
// or rule (never a field value), mapped here to text for the reviewer.
import { ApiError } from './api'

const OVER_MEMORY_BUDGET_TEXT = 'הקובץ גדול מדי לשרת ההדגמה, נסו לפצל אותו'

const DETAIL_TEXT: Record<string, string> = {
  'file too large': 'הקובץ גדול מהמותר',
  'file exceeds the memory budget': OVER_MEMORY_BUDGET_TEXT,
  'too many files': 'נבחרו יותר מדי קבצים להעלאה אחת',
  'only PDF, PNG, JPEG, TIFF or WEBP files are supported': 'ניתן להעלות רק PDF, PNG, JPEG, TIFF או WEBP',
  'job not found': 'העבודה לא נמצאה (ייתכן שנמחקה או שפג תוקפה)',
  'commitment not found': 'הטופס לא נמצא בעבודה',
  'job is not finished': 'העיבוד עדיין לא הסתיים',
  'value must not be empty': 'הערך לא יכול להיות ריק',
  'a date must be YYYY-MM-DD': 'תאריך לא תקין',
  'sender must be a canonical name from the closed list': 'יש לבחור גורם שולח מהרשימה',
  'job store unavailable': 'מאגר הנתונים אינו זמין כרגע',
  'lab system unavailable': 'מערכת המעבדה אינה זמינה כרגע',
  'too many pages': 'יותר מדי עמודים בהעלאה אחת',
  'request too large': 'ההעלאה גדולה מהמותר',
  'access token missing or invalid': 'קוד הגישה חסר או שגוי',
  'rate limit exceeded': 'יותר מדי בקשות, נסו שוב בעוד כמה דקות',
}

const STATUS_TEXT: Record<number, string> = {
  401: 'נדרש קוד גישה',
  404: 'לא נמצא',
  409: 'הפעולה אינה אפשרית כרגע',
  413: 'ההעלאה גדולה מהמותר',
  415: 'סוג קובץ לא נתמך',
  422: 'הערך לא תקין',
  429: 'יותר מדי בקשות, נסו שוב בעוד כמה דקות',
  503: 'השירות אינו זמין כרגע',
}

// A failed job's `error` is the exception type; the ones a reviewer can act on get a text.
const JOB_ERROR_TEXT: Record<string, string> = {
  PixelBudgetError: OVER_MEMORY_BUDGET_TEXT,
}

const NETWORK_TEXT = 'אין חיבור לשרת'
const UNKNOWN_TEXT = 'שגיאה לא צפויה'

// "<what failed>: <why>", e.g. "ההעלאה נכשלה: סוג קובץ לא נתמך".
export function failureText(action: string, error: unknown): string {
  return `${action}: ${reasonText(error)}`
}

// The text for a failed job's error type; an unknown type is shown as it is.
export function jobErrorText(error: string): string {
  return JOB_ERROR_TEXT[error] ?? error
}

export function reasonText(error: unknown): string {
  if (error instanceof ApiError) {
    return (error.detail && DETAIL_TEXT[error.detail]) || STATUS_TEXT[error.status] || `${UNKNOWN_TEXT} (${error.status})`
  }
  // fetch rejects with a TypeError when the server cannot be reached.
  if (error instanceof TypeError) return NETWORK_TEXT
  return UNKNOWN_TEXT
}
