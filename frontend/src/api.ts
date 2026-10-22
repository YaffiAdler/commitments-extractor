// Types mirror backend/app/schemas.py (only what the page uses).

export interface BBox {
  page: number
  x0: number
  y0: number
  x1: number
  y1: number
}

export interface Field {
  value: string | null
  needs_review: boolean
  bbox: BBox | null
}

export interface Commitment {
  first_name: Field
  last_name: Field
  id_number: Field
  central_numbers: Field[]
  received_date: Field
  sender: Field
  commitment_numbers: Field[]
  optional_fields: string[]
}

export const SPLIT_UNCERTAIN = 'split_uncertain'

export interface FormGroup {
  id: string
  pages: number[]
  split_confidence: number
  split_reasons: string[]
  commitment: Commitment
  // Reading of this form has ended; unfinished forms are listed while a job runs.
  finished: boolean
}

export interface IrrelevantPage {
  source_file: string
  page: number
}

export type JobStatus = 'pending' | 'running' | 'done' | 'failed'

export interface Job {
  id: string
  status: JobStatus
  groups: FormGroup[]
  irrelevant_pages: IrrelevantPage[]
  split_vlm_calls: number
  error: string | null
}

// Same-origin '/api' goes through the Vite dev proxy; a deployed frontend (Vercel)
// sets VITE_API_URL to the backend URL, which must list it in CE_ALLOWED_ORIGINS.
const API = (import.meta.env.VITE_API_URL || '/api').replace(/\/+$/, '')

async function checked(response: Response): Promise<Response> {
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response
}

export async function uploadFile(file: File): Promise<Job> {
  const body = new FormData()
  body.append('file', file)
  const response = await checked(await fetch(`${API}/jobs`, { method: 'POST', body }))
  return response.json()
}

export async function getJob(jobId: string): Promise<Job> {
  return (await checked(await fetch(`${API}/jobs/${jobId}`))).json()
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
    const source = new EventSource(`${API}/jobs/${jobId}/events`)
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
  return (await checked(await fetch(`${API}/jobs/${jobId}/export`, { method: 'POST' }))).blob()
}

// Detail of the crop endpoint's 404 once the page image was deleted (file TTL or a server restart).
export const IMAGE_GONE = 'image_gone'

export type CropResult = { kind: 'image'; blob: Blob } | { kind: 'gone' }

// The field's box on its page (PNG). item: index in central/commitment number lists.
export async function getCrop(jobId: string, groupId: string, field: string, item: number | null): Promise<CropResult> {
  const query = item === null ? '' : `?item=${item}`
  const response = await fetch(`${API}/jobs/${jobId}/groups/${groupId}/fields/${field}/crop${query}`)
  if (response.status === 404) {
    const body = await response.json().catch(() => null)
    if (body?.detail === IMAGE_GONE) return { kind: 'gone' }
  }
  return { kind: 'image', blob: await (await checked(response)).blob() }
}

export async function deleteJob(jobId: string): Promise<void> {
  await checked(await fetch(`${API}/jobs/${jobId}`, { method: 'DELETE' }))
}
