// Dummy jobs for the tests: invented values that belong to no real person.
import type { Commitment, Field, FormGroup, Job } from '../api'

export function field(value: string | null, review = false, extra: Partial<Field> = {}): Field {
  return {
    value,
    raw_value: value,
    confidence: review ? 0.4 : 0.95,
    needs_review: value === null || review,
    reasons: review ? ['single_reading'] : [],
    explanations: review ? ['נקרא פעם אחת בלבד'] : [],
    source: value === null ? 'none' : 'printed',
    bbox: null,
    ...extra,
  }
}

export function commitment(overrides: Partial<Commitment> = {}): Commitment {
  return {
    form_type_id: 'clalit_form17',
    first_name: field('ישראל'),
    last_name: field('ישראלי'),
    id_number: field('000000018'),
    central_numbers: [field('123456')],
    received_date: field('2025-01-01'),
    sender: field('כללית'),
    commitment_numbers: [field('0001234567')],
    optional_fields: [],
    ...overrides,
  }
}

let nextId = 0

export function group(overrides: Partial<FormGroup> = {}): FormGroup {
  nextId += 1
  return {
    id: `g${nextId}`,
    source_file: 'f1.pdf',
    pages: [nextId - 1],
    split_confidence: 1,
    split_reasons: [],
    form_reasons: [],
    commitment: commitment(),
    finished: true,
    error: null,
    ...overrides,
  }
}

export function job(groups: FormGroup[], overrides: Partial<Job> = {}): Job {
  return { id: 'j1', status: 'done', files: ['f1.pdf'], groups, irrelevant_pages: [], error: null, ...overrides }
}
