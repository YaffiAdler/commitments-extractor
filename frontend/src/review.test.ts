import { describe, expect, it } from 'vitest'
import {
  cellEntries,
  correctionValue,
  displayValue,
  formFlags,
  groupNeedsReview,
  nextReviewTarget,
  reviewTargets,
  sortGroups,
  toIsoDate,
} from './review'
import { commitment, field, group } from './test/fixtures'

describe('cells', () => {
  it('gives one entry per list item and flags each on its own', () => {
    const g = group({ commitment: commitment({ central_numbers: [field('1'), field('2', true)] }) })
    expect(cellEntries(g, 'central_numbers').map((e) => [e.target.item, e.review])).toEqual([
      [0, false],
      [1, true],
    ])
  })

  it('flags an empty list unless the field is optional, and editing it adds item 0', () => {
    const empty = group({ commitment: commitment({ commitment_numbers: [] }) })
    expect(cellEntries(empty, 'commitment_numbers')).toEqual([
      { target: { groupId: empty.id, field: 'commitment_numbers', item: 0 }, field: null, review: true },
    ])
    const optional = group({ commitment: commitment({ commitment_numbers: [], optional_fields: ['commitment_numbers'] }) })
    expect(cellEntries(optional, 'commitment_numbers')[0].review).toBe(false)
  })
})

describe('review targets', () => {
  const a = group({ commitment: commitment({ last_name: field('כהן', true), id_number: field(null) }) })
  const b = group({ commitment: commitment({ central_numbers: [field('1'), field('2', true)] }) })

  it('lists the fields to review in reading order', () => {
    expect(reviewTargets([a, b])).toEqual([
      { groupId: a.id, field: 'last_name', item: null },
      { groupId: a.id, field: 'id_number', item: null },
      { groupId: b.id, field: 'central_numbers', item: 1 },
    ])
  })

  it('walks forward and backward with wrap-around', () => {
    const first = { groupId: a.id, field: 'last_name' as const, item: null }
    expect(nextReviewTarget([a, b], null)).toEqual(first)
    expect(nextReviewTarget([a, b], first)).toEqual({ groupId: a.id, field: 'id_number', item: null })
    expect(nextReviewTarget([a, b], first, -1)).toEqual({ groupId: b.id, field: 'central_numbers', item: 1 })
    expect(nextReviewTarget([a, b], { groupId: b.id, field: 'central_numbers', item: 1 })).toEqual(first)
  })

  it('continues after a field that was just confirmed', () => {
    const confirmed = { groupId: a.id, field: 'first_name' as const, item: null } // not orange
    expect(nextReviewTarget([a, b], confirmed)).toEqual({ groupId: a.id, field: 'last_name', item: null })
  })

  it('returns null when nothing is left', () => {
    expect(nextReviewTarget([group()], null)).toBeNull()
  })
})

describe('form flags', () => {
  it('names an uncertain split and form-level reasons', () => {
    const g = group({ split_reasons: ['split_uncertain', 'single_page'], form_reasons: ['form_type_unknown'] })
    expect(formFlags(g).map((f) => f.label)).toEqual(['פיצול לא בטוח', 'סוג טופס לא מזוהה'])
    expect(groupNeedsReview(g)).toBe(true)
    expect(groupNeedsReview(group())).toBe(false)
  })
})

describe('rows', () => {
  it('sorts by file as uploaded, then by page', () => {
    const late = group({ source_file: 'f1.pdf', pages: [3] })
    const early = group({ source_file: 'f1.pdf', pages: [0] })
    const second = group({ source_file: 'f2.pdf', pages: [0] })
    expect(sortGroups([second, late, early], ['f1.pdf', 'f2.pdf']).map((g) => g.id)).toEqual([early.id, late.id, second.id])
  })
})

describe('values', () => {
  it('shows a date as DD/MM/YYYY and leaves other values alone', () => {
    expect(displayValue('received_date', '2025-03-09')).toBe('09/03/2025')
    expect(displayValue('id_number', '000000018')).toBe('000000018')
  })

  it.each([
    ['09/03/2025', '2025-03-09'],
    ['9.3.2025', '2025-03-09'],
    ['2025-03-09', '2025-03-09'],
    ['31/02/2025', null],
    ['09/03/25', null], // a two-digit year is never completed
    ['מחר', null],
  ])('reads the date %s as %s', (text, expected) => {
    expect(toIsoDate(text)).toBe(expected)
  })

  it('refuses an empty correction and keeps leading zeros', () => {
    expect(correctionValue('id_number', '   ')).toBeNull()
    expect(correctionValue('id_number', ' 000000026 ')).toBe('000000026')
    expect(correctionValue('received_date', '04/01/2026')).toBe('2026-01-04')
  })
})
