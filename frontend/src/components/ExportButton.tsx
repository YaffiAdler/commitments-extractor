import { useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'

interface Props {
  disabled: boolean
  // Fields still to review and forms with a form-level flag: a warning, never a block.
  unconfirmedFields: number
  flaggedForms: number
  onExport: () => void
}

export function ExportButton({ disabled, unconfirmedFields, flaggedForms, onExport }: Props) {
  const [warning, setWarning] = useState(false)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const openerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (warning) confirmRef.current?.focus()
  }, [warning])

  function close() {
    setWarning(false)
    openerRef.current?.focus()
  }

  function request() {
    if (unconfirmedFields > 0 || flaggedForms > 0) setWarning(true)
    else onExport()
  }

  return (
    <>
      <button type="button" ref={openerRef} className="primary" disabled={disabled} onClick={request}>
        <Icon name="download" />
        ייצוא לאקסל
      </button>
      {warning && (
        <div className="backdrop">
          <div
            className="dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="export-warning-title"
            aria-describedby="export-warning-text"
            onKeyDown={(e) => e.key === 'Escape' && close()}
          >
            <h2 id="export-warning-title">נותרו פריטים שלא אושרו</h2>
            <p id="export-warning-text">
              {unconfirmedFields > 0 && <>{unconfirmedFields} שדות עדיין מסומנים לבדיקה. </>}
              {flaggedForms > 0 && <>{flaggedForms} טפסים מסומנים בדגל. </>}
              בקובץ הם יופיעו בכתום, עם הסיבות בעמודת ההערות. ההחלטה בידייך.
            </p>
            <div className="toolbar dialog-actions">
              <button
                type="button"
                ref={confirmRef}
                className="primary"
                onClick={() => {
                  setWarning(false)
                  onExport()
                }}
              >
                ייצוא בכל זאת
              </button>
              <button type="button" onClick={close}>
                חזרה לבדיקה
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
