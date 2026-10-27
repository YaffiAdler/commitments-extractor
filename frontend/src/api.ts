// Types mirror backend/app/schemas.py (only what the page uses).
import { loadToken, reportAccessDenied } from './access'

export interface BBox {
  page: number
  x0: number
  y0: number
  x1: number
  y1: number
}

export type FieldSource = 'printed' | 'handwritten' | 'barcode' | 'stamp' | 'sticker' | 'manual' | 'none'

// One independent reading kept next to a field's value (OCR, second look, name second look).
export interface FieldSignal {
  source: 'vlm' | 'ocr' | 'reread' | 'name_list'
  value: string | null
  // Name second look only: the crop variant, the ranked candidates (value first) and the uncertain letters.
  variant: string | null
  candidates: string[]
  uncertain: number[]
}

export interface Field {
  value: string | null
  raw_value: string | null
  confidence: number
  needs_review: boolean
  reasons: string[]
  // The reasons as Hebrew text (set by the confidence stage and by a correction).
  explanations: string[]
  source: FieldSource
  bbox: BBox | null
  signals: FieldSignal[]
}

export const SINGLE_FIELDS = ['first_name', 'last_name', 'id_number', 'received_date', 'sender'] as const
export const LIST_FIELDS = ['central_numbers', 'commitment_numbers'] as const
export type SingleFieldName = (typeof SINGLE_FIELDS)[number]
export type ListFieldName = (typeof LIST_FIELDS)[number]
export type FieldName = SingleFieldName | ListFieldName

export type Commitment = { [K in SingleFieldName]: Field } & { [K in ListFieldName]: Field[] } & {
  form_type_id: string | null
  optional_fields: string[]
}

export interface FormGroup {
  id: string
  source_file: string
  pages: number[]
  split_confidence: number
  split_reasons: string[]
  form_reasons: string[]
  commitment: Commitment
  // Reading of this form has ended; unfinished forms are listed while a job runs.
  finished: boolean
  error: string | null
}

export interface IrrelevantPage {
  source_file: string
  page: number
}

export type JobStatus = 'pending' | 'running' | 'done' | 'failed'

export interface Job {
  id: string
  status: JobStatus
  files: string[]
  groups: FormGroup[]
  irrelevant_pages: IrrelevantPage[]
  error: string | null
}

// A deployed frontend (Vercel) calls the backend directly at VITE_API_URL, which must list the
// frontend in CE_ALLOWED_ORIGINS; `vite build` refuses to run without it (vite.config.ts).
// Only the dev server falls back to same-origin '/api', its local proxy.
const API = (import.meta.env.VITE_API_URL || '/api').replace(/\/+$/, '')
// Query parameter for the access code where no header can be sent (EventSource).
const TOKEN_QUERY_PARAM = 'access_token'

// A failed request: the HTTP status and the backend's `detail` (a fixed code or rule, never a value).
export class ApiError extends Error {
  readonly status: number
  readonly detail: string | null

  constructor(status: number, detail: string | null) {
    super(`HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

async function checked(response: Response): Promise<Response> {
  if (response.ok) return response
  if (response.status === 401) reportAccessDenied()
  const body = await response.json().catch(() => null)
  throw new ApiError(response.status, typeof body?.detail === 'string' ? body.detail : null)
}

// fetch on the backend with the access code (if any) as a Bearer header; rejects with ApiError.
async function request(path: string, init: RequestInit = {}, token: string | null = loadToken()): Promise<Response> {
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return checked(await fetch(`${API}${path}`, { ...init, headers }))
}

// Resolves if the server accepts `token` (or needs none); ApiError 401 if it refuses it.
export async function checkAccess(token: string | null): Promise<void> {
  await request('/senders', {}, token)
}

export async function uploadFiles(files: File[]): Promise<Job> {
  const body = new FormData()
  for (const file of files) body.append('files', file)
  return (await request('/jobs', { method: 'POST', body })).json()
}

export async function getJob(jobId: string): Promise<Job> {
  return (await request(`/jobs/${jobId}`)).json()
}

export async function getSenders(): Promise<string[]> {
  return (await request('/senders')).json()
}

// Mirrors backend/app/review_levels.py (thresholds from config.ReviewLevelSettings).
export interface ReviewLevelConfig {
  glance_min_confidence: number
  verify_below_confidence: number
  must_verify_reasons: string[]
  show_confidence_percent: boolean
}

export async function getReviewLevels(): Promise<ReviewLevelConfig> {
  return (await request('/review-levels')).json()
}

// Mirrors backend/app/events.py.
export interface JobProgress {
  status: JobStatus
  forms_total: number
  forms_done: number
}

export interface JobStreamHandlers {
  onProgress: (progress: JobProgress) => void
  // Forms whose reading finished since the previous call, each sent once.
  onRows: (rows: FormGroup[]) => void
}

// Follows GET /jobs/{id}/events. Resolves with the finished job ('end' event).
// Rejects if EventSource is missing, the job is gone, or the stream breaks
// before the end; the caller then falls back to polling.
export function streamJob(jobId: string, handlers: JobStreamHandlers): Promise<Job> {
  return new Promise((resolve, reject) => {
    if (typeof EventSource === 'undefined') {
      reject(new Error('EventSource is not supported'))
      return
    }
    // EventSource cannot send headers, so the code goes in the query (the backend masks it in its logs).
    const token = loadToken()
    const query = token ? `?${new URLSearchParams({ [TOKEN_QUERY_PARAM]: token })}` : ''
    const source = new EventSource(`${API}/jobs/${jobId}/events${query}`)
    const data = (event: Event) => JSON.parse((event as MessageEvent<string>).data)
    const fail = (message: string) => {
      source.close()
      reject(new Error(message))
    }
    source.addEventListener('progress', (event) => handlers.onProgress(data(event)))
    source.addEventListener('rows', (event) => handlers.onRows(data(event)))
    source.addEventListener('end', (event) => {
      source.close()
      resolve(data(event))
    })
    source.addEventListener('missing', () => fail('job not found'))
    // Without close() the browser would reconnect on its own; polling takes over instead.
    source.onerror = () => fail('event stream failed')
  })
}

export async function exportJob(jobId: string): Promise<Blob> {
  return (await request(`/jobs/${jobId}/export`, { method: 'POST' })).blob()
}

// The reviewer's value for one field; item is the list index (or the list length to add one).
export async function correctField(
  jobId: string,
  commitmentId: string,
  field: FieldName,
  value: string,
  item: number | null,
): Promise<Job> {
  const response = await request(`/jobs/${jobId}/commitments/${commitmentId}/fields/${field}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(item === null ? { value } : { value, item }),
  })
  return response.json()
}

// Mirrors backend/app/lab_system (base.py, matching.py). Dates are YYYY-MM-DD.
export interface ReceivedSample {
  central_number: string
  received_date: string
  sender: string
}

export interface MatchedSample {
  commitment_id: string
  central_number: string
  sample: ReceivedSample
  commitment_received_date: string | null
  // null when the form's received date was not read.
  date_differs: boolean | null
}

export interface CommitmentWithoutSample {
  commitment_id: string
  // null when no central number was read on the form.
  central_number: string | null
}

export interface LabMatch {
  matched: MatchedSample[]
  samples_without_commitment: ReceivedSample[]
  commitments_without_sample: CommitmentWithoutSample[]
}

// The job's commitments checked against the lab system's samples by central number (read-only).
export async function getLabMatch(jobId: string): Promise<LabMatch> {
  return (await request(`/jobs/${jobId}/lab-match`)).json()
}

// Detail of the image endpoints' 404 once the page image was deleted (file TTL or a server restart).
export const IMAGE_GONE = 'image_gone'
export const NO_BOX = 'no_box'

export type ImageResult = { kind: 'image'; blob: Blob } | { kind: 'missing'; detail: string | null }

async function imageAt(path: string): Promise<ImageResult> {
  try {
    return { kind: 'image', blob: await (await request(path)).blob() }
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return { kind: 'missing', detail: e.detail }
    throw e
  }
}

// The field's box on its page (PNG). item: index in central/commitment number lists.
export function getCrop(jobId: string, commitmentId: string, field: FieldName, item: number | null) {
  const query = item === null ? '' : `?item=${item}`
  return imageAt(`/jobs/${jobId}/crop/${commitmentId}/${field}${query}`)
}

// A whole page of the form (JPEG) at the resolution of Field.bbox; page is 0-based in the source file.
export function getPage(jobId: string, commitmentId: string, page: number) {
  return imageAt(`/jobs/${jobId}/pages/${commitmentId}/${page}`)
}

export async function deleteJob(jobId: string): Promise<void> {
  await request(`/jobs/${jobId}`, { method: 'DELETE' })
}
