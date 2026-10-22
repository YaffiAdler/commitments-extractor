import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import { DEMO_ACCESS_TOKEN, DEMO_BACKEND_URL } from '../playwright.demo.config.ts'

// Run with `npm run e2e:demo` (playwright.demo.config.ts).
const FORM = fileURLToPath(new URL('./fixtures/dummy_form.png', import.meta.url))

test('demo: banner, access code, direct cross-origin calls and the event stream', async ({ page }) => {
  const apiCalls: string[] = []
  page.on('request', (request) => {
    if (request.url().startsWith(DEMO_BACKEND_URL)) apiCalls.push(request.url())
  })

  await page.goto('/')
  await expect(page.getByRole('note')).toHaveText('הדגמה, יש להעלות רק טפסי דוגמה, לא נתוני אמת')

  const code = page.getByLabel('קוד גישה להדגמה')
  await code.fill('wrong-code-0123456789')
  await code.press('Enter')
  await expect(page.getByRole('alert')).toHaveText('קוד הגישה שגוי')

  await page.getByLabel('קוד גישה להדגמה').fill(DEMO_ACCESS_TOKEN)
  await page.getByRole('button', { name: 'כניסה' }).click()
  await page.getByLabel('בחירת קבצים').setInputFiles(FORM)
  await page.getByRole('button', { name: 'התחלת עיבוד' }).click()
  await expect(page.getByRole('table').getByRole('row')).toHaveCount(2)
  await expect(page.getByText('העיבוד הסתיים')).toBeVisible()

  // The code is in sessionStorage only, and the page called the backend directly, the stream with the code in the query.
  const storage = await page.evaluate(() => ({ session: sessionStorage.length, local: localStorage.length }))
  expect(storage).toEqual({ session: 1, local: 0 })
  expect(apiCalls).toContain(`${DEMO_BACKEND_URL}/jobs`)
  expect(apiCalls.some((url) => url.includes('/events?access_token='))).toBe(true)

  // A reload in the same tab keeps the code.
  await page.reload()
  await expect(page.getByLabel('בחירת קבצים')).toBeAttached()
})

test('demo: the backend refuses requests without the code', async ({ request }) => {
  expect((await request.get(`${DEMO_BACKEND_URL}/senders`)).status()).toBe(401)
  expect((await request.get(`${DEMO_BACKEND_URL}/health`)).status()).toBe(200)
})
