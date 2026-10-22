import { useState } from 'react'
import { type FileRejection, useDropzone } from 'react-dropzone'
import type { JobProgress } from '../api'
import { Icon } from './Icon'

// The types the backend accepts (it checks the content, not the name).
const ACCEPT = {
  'application/pdf': ['.pdf'],
  'image/png': ['.png'],
  'image/jpeg': ['.jpg', '.jpeg'],
  'image/tiff': ['.tif', '.tiff'],
  'image/webp': ['.webp'],
}
const BYTES_PER_KB = 1024

const sizeText = (bytes: number) =>
  bytes < BYTES_PER_KB * BYTES_PER_KB
    ? `${Math.max(1, Math.round(bytes / BYTES_PER_KB))} KB`
    : `${(bytes / BYTES_PER_KB / BYTES_PER_KB).toFixed(1)} MB`

const sameFile = (a: File, b: File) => a.name === b.name && a.size === b.size && a.lastModified === b.lastModified

interface Props {
  busy: boolean
  progress: JobProgress | null
  onUpload: (files: File[]) => void
}

export function UploadPanel({ busy, progress, onUpload }: Props) {
  const [files, setFiles] = useState<File[]>([])
  const [rejected, setRejected] = useState<string[]>([])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: ACCEPT,
    disabled: busy,
    onDrop: (accepted: File[], rejections: FileRejection[]) => {
      setFiles((current) => [...current, ...accepted.filter((f) => !current.some((c) => sameFile(c, f)))])
      setRejected(rejections.map((r) => r.file.name))
    },
  })

  function start() {
    onUpload(files)
    setFiles([])
    setRejected([])
  }

  return (
    <section className="card upload" aria-label="העלאת קבצים">
      <div {...getRootProps({ className: `dropzone${isDragActive ? ' active' : ''}${busy ? ' disabled' : ''}` })}>
        <input {...getInputProps({ 'aria-label': 'בחירת קבצים' })} />
        <span className="dropzone-icon">
          <Icon name="upload" size={22} />
        </span>
        <span className="dropzone-title">{isDragActive ? 'אפשר לשחרר כאן' : 'גררו לכאן קובצי PDF או תמונות'}</span>
        <span className="muted small">או לחצו לבחירה · אפשר כמה קבצים · PDF, PNG, JPEG, TIFF, WEBP</span>
      </div>

      {rejected.length > 0 && (
        <p className="error" role="alert">
          קבצים שלא נוספו (סוג לא נתמך): <bdi>{rejected.join(', ')}</bdi>
        </p>
      )}

      {files.length > 0 && (
        <ul className="file-list" aria-label="קבצים שנבחרו">
          {files.map((file, index) => (
            <li key={`${file.name}-${file.size}-${file.lastModified}`}>
              <Icon name="file" size={16} />
              <bdi className="file-name">{file.name}</bdi> <span className="muted small">{sizeText(file.size)}</span>
              <button
                type="button"
                className="link"
                aria-label={`הסרת ${file.name}`}
                onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}
              >
                הסרה
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="toolbar">
        <button type="button" className="primary" disabled={busy || files.length === 0} onClick={start}>
          {files.length > 1 ? `עיבוד ${files.length} קבצים` : 'התחלת עיבוד'}
        </button>
        {progress && <ProgressBar progress={progress} />}
      </div>
    </section>
  )
}

function ProgressBar({ progress }: { progress: JobProgress }) {
  const { status, forms_done, forms_total } = progress
  if (status === 'done' || status === 'failed') return null
  const known = status === 'running' && forms_total > 0
  const label =
    status === 'pending' ? 'ממתין בתור…' : known ? `נקראו ${forms_done} מתוך ${forms_total} טפסים` : 'מחלק את הקבצים לטפסים…'
  return (
    <div className="progress">
      <div
        className={`bar${known ? '' : ' indeterminate'}`}
        role="progressbar"
        aria-label="התקדמות העיבוד"
        aria-valuemin={0}
        aria-valuemax={known ? forms_total : undefined}
        aria-valuenow={known ? forms_done : undefined}
        aria-valuetext={label}
      >
        <span style={{ width: known ? `${(100 * forms_done) / forms_total}%` : undefined }} />
      </div>
      <span aria-live="polite">{label}</span>
    </div>
  )
}
