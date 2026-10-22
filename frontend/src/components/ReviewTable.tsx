import { createColumnHelper, tableFeatures, useTable } from '@tanstack/react-table'
import { type KeyboardEvent, useMemo } from 'react'
import type { FormGroup } from '../api'
import { Icon } from './Icon'
import {
  type CellEntry,
  type Column,
  COLUMNS,
  type FieldTarget,
  cellEntries,
  displayValue,
  formFlags,
  groupNeedsReview,
  pageNumbers,
  reviewTargets,
  sameTarget,
  targetKey,
} from '../review'

const features = tableFeatures({})
const helper = createColumnHelper<typeof features, FormGroup>()

interface CellProps {
  group: FormGroup
  column: Column
  selected: FieldTarget | null
  onSelect: (target: FieldTarget) => void
}

const MISSING_TEXT = '—'

function entryLabel(column: Column, entry: CellEntry, count: number): string {
  const name = entry.target.item !== null && count > 1 ? `${column.header} ${entry.target.item + 1}` : column.header
  const value = entry.field?.value ? displayValue(column.key, entry.field.value) : 'חסר'
  return `${name}: ${value}${entry.review ? ' (לבדיקה)' : ''}`
}

function FieldCell({ group, column, selected, onSelect }: CellProps) {
  const entries = cellEntries(group, column.key)
  return (
    <div className="entries">
      {entries.map((entry) => (
        <button
          type="button"
          key={targetKey(entry.target)}
          className={`entry${entry.review ? ' review' : ''}${entry.field?.source === 'manual' ? ' manual' : ''}`}
          data-target={targetKey(entry.target)}
          aria-pressed={sameTarget(entry.target, selected)}
          aria-label={entryLabel(column, entry, entries.length)}
          onClick={() => onSelect(entry.target)}
        >
          {entry.field?.value ? (
            <span dir={column.ltr ? 'ltr' : undefined}>{displayValue(column.key, entry.field.value)}</span>
          ) : (
            MISSING_TEXT
          )}
        </button>
      ))}
    </div>
  )
}

function FlagsCell({ group }: { group: FormGroup }) {
  const flags = formFlags(group)
  return (
    <ul className="flags">
      {flags.map((flag) => (
        <li key={flag.code} className="flag" title={flag.explanation}>
          {flag.label}
        </li>
      ))}
    </ul>
  )
}

// Arrow keys move between the value buttons like a grid; in RTL, left is the next column.
const ARROW_STEPS: Record<string, [number, number]> = {
  ArrowUp: [-1, 0],
  ArrowDown: [1, 0],
  ArrowLeft: [0, 1],
  ArrowRight: [0, -1],
}

function moveFocus(event: KeyboardEvent<HTMLTableElement>) {
  const step = ARROW_STEPS[event.key]
  const current = (event.target as HTMLElement).closest<HTMLElement>('button.entry')
  const cell = current?.closest<HTMLElement>('td[data-row]')
  if (!step || !current || !cell) return
  const row = Number(cell.dataset.row) + step[0]
  const col = Number(cell.dataset.col) + step[1]
  const next = event.currentTarget.querySelector<HTMLElement>(`td[data-row="${row}"][data-col="${col}"] button.entry`)
  if (next) {
    event.preventDefault()
    next.focus()
  }
}

interface Props {
  groups: FormGroup[]
  // Several files: each row names its file by number.
  fileIndex: (group: FormGroup) => number
  multipleFiles: boolean
  selected: FieldTarget | null
  onSelect: (target: FieldTarget) => void
  onlyUncertain: boolean
  onOnlyUncertainChange: (value: boolean) => void
}

export function ReviewTable(props: Props) {
  const { groups, fileIndex, multipleFiles, selected, onSelect, onlyUncertain, onOnlyUncertainChange } = props
  const toReview = reviewTargets(groups).length
  const flagged = groups.filter((g) => formFlags(g).length > 0).length
  const data = useMemo(() => (onlyUncertain ? groups.filter(groupNeedsReview) : groups), [groups, onlyUncertain])

  const columns = useMemo(
    () =>
      helper.columns([
        ...COLUMNS.map((column) =>
          helper.display({
            id: column.key,
            header: column.header,
            cell: ({ row }) => <FieldCell group={row.original} column={column} selected={selected} onSelect={onSelect} />,
          }),
        ),
        helper.display({ id: 'flags', header: 'דגלי טופס', cell: ({ row }) => <FlagsCell group={row.original} /> }),
        helper.display({
          id: 'pages',
          header: 'עמודים',
          cell: ({ row }) =>
            multipleFiles
              ? `קובץ ${fileIndex(row.original) + 1}, ${pageNumbers(row.original.pages)}`
              : pageNumbers(row.original.pages),
        }),
      ]),
    [selected, onSelect, multipleFiles, fileIndex],
  )

  const table = useTable({ features, columns, data, getRowId: (group) => group.id })

  return (
    <section className="card review-table" aria-label="טבלת התחייבויות">
      <div className="card-header">
        <h3 className="card-title">
          <Icon name="table" />
          התחייבויות
          <span className="count-badge">{groups.length}</span>
        </h3>
        <div className="toolbar">
          <span className={`counter${toReview > 0 ? ' review' : ''}`} role="status">
            {toReview} לבדיקה
          </span>
          {flagged > 0 && <span className="counter flag">{flagged} טפסים עם דגל</span>}
          <label className="toggle">
            <input type="checkbox" checked={onlyUncertain} onChange={(e) => onOnlyUncertainChange(e.target.checked)} />
            <span className="toggle-track" aria-hidden="true" />
            הצג רק לא בטוחים
          </label>
        </div>
      </div>

      {data.length === 0 ? (
        <p className="muted empty">{onlyUncertain ? 'אין שורות לבדיקה.' : 'אין עדיין שורות.'}</p>
      ) : (
        <div className="table-scroll">
          <table onKeyDown={moveFocus}>
            <thead>
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id}>
                  {headerGroup.headers.map((header) => (
                    <th key={header.id} scope="col">
                      <table.FlexRender header={header} />
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row, rowIndex) => (
                <tr key={row.id} className={formFlags(row.original).length > 0 ? 'flagged' : undefined}>
                  {row.getAllCells().map((cell, colIndex) => {
                    const column = COLUMNS[colIndex]
                    const review = column ? cellEntries(row.original, column.key).some((e) => e.review) : false
                    return (
                      <td
                        key={cell.id}
                        className={review ? 'review' : undefined}
                        data-row={column ? rowIndex : undefined}
                        data-col={column ? colIndex : undefined}
                      >
                        <table.FlexRender cell={cell} />
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
