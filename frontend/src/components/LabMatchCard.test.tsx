import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { group } from '../test/fixtures'
import { LAB_DEMO_LABEL, LabMatchCard } from './LabMatchCard'

vi.mock('../api', async (importOriginal) => ({ ...(await importOriginal<typeof api>()), getLabMatch: vi.fn() }))

describe('LabMatchCard', () => {
  it('shows the demo label, three counters and the groups after the check', async () => {
    const first = group()
    const second = group()
    vi.mocked(api.getLabMatch).mockResolvedValue({
      matched: [{
        commitment_id: first.id,
        central_number: '123456',
        sample: { central_number: '123456', received_date: '2025-01-02', sender: 'כללית' },
        commitment_received_date: '2025-01-01',
        date_differs: true,
      }],
      samples_without_commitment: [{ central_number: '456789', received_date: '2025-01-01', sender: 'מכבי' }],
      commitments_without_sample: [{ commitment_id: second.id, central_number: null }],
    })
    render(<LabMatchCard jobId="j1" groups={[first, second]} disabled={false} />)
    expect(screen.queryByText(LAB_DEMO_LABEL)).toBeNull()

    await userEvent.setup().click(screen.getByRole('button', { name: 'בדיקה מול מערכת המעבדה' }))

    expect(await screen.findByText(LAB_DEMO_LABEL)).toBeTruthy()
    expect(api.getLabMatch).toHaveBeenCalledWith('j1')
    const matched = screen.getByRole('region', { name: 'מותאמות' })
    expect(within(matched).getByText(/טופס 1 · מרכזי 123456 · תאריך שונה: בטופס 01\/01\/2025, במעבדה 02\/01\/2025/)).toBeTruthy()
    expect(screen.getByText('מרכזי 456789 · 01/01/2025 · מכבי')).toBeTruthy()
    expect(screen.getByText('טופס 2 · לא נקרא מספר מרכזי')).toBeTruthy()
    expect(screen.getAllByRole('definition').map((d) => d.textContent)).toEqual(['1', '1', '1'])
  })
})
