import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { FormGroup } from '../api'
import type { FieldTarget } from '../review'
import { commitment, field, group } from '../test/fixtures'
import { ReviewTable } from './ReviewTable'

function Harness({ groups, onSelect = () => {} }: { groups: FormGroup[]; onSelect?: (t: FieldTarget) => void }) {
  const [onlyUncertain, setOnlyUncertain] = useState(false)
  return (
    <ReviewTable
      groups={groups}
      fileIndex={() => 0}
      multipleFiles={false}
      selected={null}
      onSelect={onSelect}
      onlyUncertain={onlyUncertain}
      onOnlyUncertainChange={setOnlyUncertain}
    />
  )
}

const uncertain = () =>
  group({ commitment: commitment({ id_number: field('000000019', true), commitment_numbers: [] }) })

describe('ReviewTable', () => {
  it('renders one row per commitment and paints fields to review orange', () => {
    render(<Harness groups={[group(), uncertain()]} />)
    const rows = screen.getAllByRole('row').slice(1) // without the header row
    expect(rows).toHaveLength(2)
    const id = within(rows[1]).getByRole('button', { name: 'ת.ז.: 000000019 (לבדיקה)' })
    expect(id.className).toContain('review')
    expect(id.closest('td')?.className).toBe('review')
    expect(within(rows[0]).getByRole('button', { name: 'ת.ז.: 000000018' }).closest('td')?.className).toBe('')
    // An empty commitment-number list is a cell to review too.
    expect(within(rows[1]).getByRole('button', { name: 'מספר התחייבות: חסר (לבדיקה)' })).toBeTruthy()
  })

  it('counts the fields to review', () => {
    render(<Harness groups={[group(), uncertain()]} />)
    expect(screen.getByRole('status').textContent).toBe('2 לבדיקה')
  })

  it('shows numbers left-to-right inside the right-to-left cell', () => {
    render(<Harness groups={[group()]} />)
    const value = screen.getByText('000000018')
    expect(value.getAttribute('dir')).toBe('ltr')
    expect(screen.getByText('01/01/2025').getAttribute('dir')).toBe('ltr')
    expect(screen.getByText('ישראל').getAttribute('dir')).toBeNull()
  })

  it('filters to uncertain rows with the toggle', async () => {
    const user = userEvent.setup()
    render(<Harness groups={[group(), uncertain(), group({ split_reasons: ['split_uncertain'] })]} />)
    expect(screen.getAllByRole('row')).toHaveLength(4)
    await user.click(screen.getByRole('checkbox', { name: 'הצג רק לא בטוחים' }))
    expect(screen.getAllByRole('row')).toHaveLength(3) // the uncertain fields and the uncertain split
    await user.click(screen.getByRole('checkbox', { name: 'הצג רק לא בטוחים' }))
    expect(screen.getAllByRole('row')).toHaveLength(4)
  })

  it('flags a form whose split or type is uncertain', () => {
    render(<Harness groups={[group({ split_reasons: ['split_uncertain'], form_reasons: ['form_type_unknown'] })]} />)
    expect(screen.getByText('פיצול לא בטוח').getAttribute('title')).toContain('ייתכן שפוצל לא נכון')
    expect(screen.getByText('סוג טופס לא מזוהה')).toBeTruthy()
    expect(screen.getByText('1 טפסים עם דגל')).toBeTruthy()
  })

  it('selects a field by click or keyboard and moves with the arrow keys', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    const g = group({ commitment: commitment({ central_numbers: [field('1'), field('2')] }) })
    render(<Harness groups={[g]} onSelect={onSelect} />)
    await user.click(screen.getByRole('button', { name: 'מספר מרכזי 2: 2' }))
    expect(onSelect).toHaveBeenLastCalledWith({ groupId: g.id, field: 'central_numbers', item: 1 })

    screen.getByRole('button', { name: 'שם פרטי: ישראל' }).focus()
    await user.keyboard('{ArrowLeft}') // RTL: left is the next column
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'שם משפחה: ישראלי' }))
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenLastCalledWith({ groupId: g.id, field: 'last_name', item: null })
    await user.keyboard('{ArrowRight}')
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'שם פרטי: ישראל' }))
  })
})
