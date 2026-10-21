// Types mirror backend/app/schemas.py (only what the page uses).

export interface Field {
  value: string | null
  needs_review: boolean
}

export interface Commitment {
  first_name: Field
  last_name: Field
  id_number: Field
  central_numbers: Field[]
  received_date: Field
  sender: Field
  commitment_numbers: Field[]
}

export const SPLIT_UNCERTAIN = 'split_uncertain'

export interface FormGroup {
  id: string
  pages: number[]
  split_confidence: number
  split_reasons: string[]
  commitment: Commitment
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

const API = '/api'

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

export async function exportJob(jobId: string): Promise<Blob> {
  return (await checked(await fetch(`${API}/jobs/${jobId}/export`, { method: 'POST' }))).blob()
}

export async function deleteJob(jobId: string): Promise<void> {
  await checked(await fetch(`${API}/jobs/${jobId}`, { method: 'DELETE' }))
}
