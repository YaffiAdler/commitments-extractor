import { describe, expect, it } from 'vitest'
import type * as api from './api'
import type { ReviewLevelConfig } from './api'
import {
  cellEntries,
  confidencePercent,
  correctionValue,
  displayValue,
  formFlags,
  groupNeedsReview,
  levelCounts,
  MAX_NAME_CHOICES,
  nameCandidates,
  nextReviewTarget,
  reviewLevel,
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

describe('review levels', () => {
  const config: ReviewLevelConfig = {
    glance_min_confidence: 0.9,
    verify_below_confidence: 0.5,
    must_verify_reasons: ['ocr_disagrees', 'illegible'],
    show_confidence_percent: false,
  }
  const levelOf = (f: ReturnType<typeof field>) =>
    reviewLevel({ target: { groupId: 'g', field: 'id_number', item: null }, field: f, review: f.needs_review }, config)

  it('asks only a quick glance for a high score that is not flagged', () => {
    expect(levelOf(field('000000018', false, { confidence: 0.9 }))).toBe('glance')
  })

  it('asks to check carefully between the thresholds, or when the confidence stage flagged it', () => {
    expect(levelOf(field('000000018', true, { confidence: 0.55, reasons: ['handwritten', 'single_reading'] }))).toBe('careful')
    expect(levelOf(field('000000018', false, { confidence: 0.89 }))).toBe('careful')
    expect(levelOf(field('000000018', true, { confidence: 0.95 }))).toBe('careful')
  })

  it('asks to verify below the lower threshold', () => {
    expect(levelOf(field('000000018', true, { confidence: 0.49 }))).toBe('verify')
    expect(levelOf(field('000000018', true, { confidence: 0.5 }))).toBe('careful')
  })

  it('asks to verify on a must-verify signal, whatever the score', () => {
    expect(levelOf(field('000000018', false, { confidence: 1, reasons: ['ocr_disagrees'] }))).toBe('verify')
  })

  it('asks to verify a missing value, and only a glance for a missing optional one', () => {
    expect(levelOf(field(null))).toBe('verify')
    const optional = group({ commitment: commitment({ commitment_numbers: [], optional_fields: ['commitment_numbers'] }) })
    expect(reviewLevel(cellEntries(optional, 'commitment_numbers')[0], config)).toBe('glance')
  })

  it('has no level once corrected by hand', () => {
    expect(levelOf(field('000000026', false, { source: 'manual', confidence: 1 }))).toBeNull()
  })

  it('counts the entries of one form per level, list items each', () => {
    const g = group({
      commitment: commitment({
        first_name: field('יוסף', true, { confidence: 0.55 }),
        id_number: field('000000018', true, { confidence: 0.3 }),
        received_date: field(null),
        sender: field('כללית', false, { source: 'manual' }),
        central_numbers: [field('123456'), field('654321', true, { confidence: 0.8 })],
      }),
    })
    // glance: last_name, central 1, commitment 1; careful: first_name, central 2; verify: id, date.
    expect(levelCounts(g, config)).toEqual({ verify: 2, careful: 2, glance: 3, manual: 1 })
  })

  it('gives a percentage only when the setting allows it', () => {
    const f = field('000000018', true, { confidence: 0.554 })
    expect(confidencePercent(f, config)).toBeNull()
    const shown = { ...config, show_confidence_percent: true }
    expect(confidencePercent(f, shown)).toBe(55)
    expect(confidencePercent(field(null), shown)).toBeNull()
    expect(confidencePercent(field('1', false, { source: 'manual' }), shown)).toBeNull()
  })
})

describe('name candidates', () => {
  const reading = (value: string | null, candidates: string[], variant: string | null = 'wide'): api.FieldSignal => ({
    source: 'reread',
    value,
    variant,
    candidates,
    uncertain: [],
  })

  it('ranks the second look readings by support and leaves out the current value', () => {
    const name = field('רוד', true, {
      signals: [reading('דוד', ['דוד', 'רוד']), reading('דוד', ['דוד', 'דור']), reading('רוד', ['רוד', 'דוב'], 'letters')],
    })
    expect(nameCandidates(name)).toEqual(['דוד', 'דור', 'דוב'])
  })

  it('ignores signals of other stages, empty readings and an empty field', () => {
    const ocr = { ...reading('123', []), source: 'ocr' as const, variant: null }
    expect(nameCandidates(field('דוד', true, { signals: [ocr, reading(null, [])] }))).toEqual([])
    expect(nameCandidates(null)).toEqual([])
  })

  it('offers at most a few choices', () => {
    const many = field('א', true, { signals: [reading('ב', ['ב', 'ג', 'ד']), reading('ה', ['ה', 'ו', 'ז'])] })
    expect(nameCandidates(many)).toHaveLength(MAX_NAME_CHOICES)
  })
})
