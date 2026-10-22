import { readFile, stat } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

// The fake provider reads the same dummy values from every page: 6 of the 7
// fields come out orange (one reading only, no OCR), the sender does not.
const FORM = fileURLToPath(new URL('./fixtures/dummy_form.png', import.meta.url))
const SCREENSHOTS = fileURLToPath(new URL('../../docs/screenshots/', import.meta.url))
const FAKE_ID = '000000018'
const CORRECTED_ID = '000000026'

test('upload → correct an orange field → download the Excel file', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')

  await page.getByLabel('בחירת קבצים').setInputFiles(FORM)
  await expect(page.getByRole('list', { name: 'קבצים שנבחרו' })).toContainText('dummy_form.png')
  await page.getByRole('button', { name: 'התחלת עיבוד' }).click()

  const table = page.getByRole('table')
  await expect(table.getByRole('row')).toHaveCount(2)
  await expect(page.getByRole('status').filter({ hasText: 'לבדיקה' })).toHaveText('6 לבדיקה')
  await expect(page.getByText('העיבוד הסתיים')).toBeVisible()
  await page.screenshot({ path: `${SCREENSHOTS}review_table.png`, fullPage: true })

  // Open the orange ID cell, type the right value, confirm with Enter.
  await page.getByRole('button', { name: `ת.ז.: ${FAKE_ID} (לבדיקה)` }).click()
  const panel = page.getByRole('complementary', { name: 'פרטי השדה ת.ז.' })
  await expect(panel).toBeVisible()
  await expect(panel.getByAltText('עמוד 1 של הטופס')).toBeVisible()
  await page.screenshot({ path: `${SCREENSHOTS}side_panel.png`, fullPage: true })
  const input = panel.getByRole('textbox', { name: 'ערך' })
  await expect(input).toBeFocused()
  await input.fill(CORRECTED_ID)
  await input.press('Enter')

  // The field is no longer uncertain; the panel moved on to the next orange field.
  const idCell = page.getByRole('button', { name: `ת.ז.: ${CORRECTED_ID}` })
  await expect(idCell).toBeVisible()
  await expect(idCell).not.toHaveClass(/review/)
  await expect(page.getByRole('status').filter({ hasText: 'לבדיקה' })).toHaveText('5 לבדיקה')
  await expect(page.getByRole('complementary', { name: 'פרטי השדה מספר מרכזי' })).toBeVisible()

  // The value as read is kept next to the correction.
  await idCell.click()
  const corrected = page.getByRole('complementary', { name: 'פרטי השדה ת.ז.' })
  await expect(corrected.getByText('תוקן ידנית')).toBeVisible()
  await expect(corrected.locator('.raw-value')).toHaveText(FAKE_ID)
  await corrected.getByRole('textbox', { name: 'ערך' }).press('Escape')

  // Five fields are still orange: a warning, and the choice stays with the reviewer.
  await page.getByRole('button', { name: 'ייצוא לאקסל' }).click()
  const dialog = page.getByRole('alertdialog')
  await expect(dialog).toContainText('5 שדות עדיין מסומנים לבדיקה')
  await page.screenshot({ path: `${SCREENSHOTS}export_warning.png` })
  const downloading = page.waitForEvent('download')
  await dialog.getByRole('button', { name: 'ייצוא בכל זאת' }).click()
  const download = await downloading
  expect(download.suggestedFilename()).toBe('commitments.xlsx')
  const saved = test.info().outputPath('commitments.xlsx')
  await download.saveAs(saved)
  expect((await stat(saved)).size).toBeGreaterThan(0)
})

test('several files make one job, one row per form', async ({ page }) => {
  await page.goto('/')
  const buffer = await readFile(FORM)
  const files = ['first.png', 'second.png'].map((name) => ({ name, mimeType: 'image/png', buffer }))
  await page.getByLabel('בחירת קבצים').setInputFiles(files)
  await page.getByRole('button', { name: 'עיבוד 2 קבצים' }).click()
  await expect(page.getByRole('table').getByRole('row')).toHaveCount(3)
  await expect(page.getByRole('cell', { name: 'קובץ 2, 1' })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: 'לבדיקה' })).toHaveText('12 לבדיקה')
})
