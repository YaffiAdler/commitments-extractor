export const DEMO_BANNER_TEXT = 'הדגמה, יש להעלות רק טפסי דוגמה, לא נתוני אמת'

// Always on screen, above the access screen and the app.
export function DemoBanner() {
  return (
    <div className="demo-banner" role="note">
      {DEMO_BANNER_TEXT}
    </div>
  )
}
