import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// jsdom has no object URLs; the images are never rendered for real in these tests.
URL.createObjectURL = vi.fn(() => 'blob:test')
URL.revokeObjectURL = vi.fn()
// jsdom cannot follow the download link of an export.
HTMLAnchorElement.prototype.click = vi.fn()

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
