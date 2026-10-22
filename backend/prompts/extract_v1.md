Read this scanned commitment form (in Hebrew), sent to the Central Virology Laboratory, and record its fields in the required output format.

## Rules
1. Do not guess. If a value is unclear or is not on the form, return null for it. A list field with nothing on the form is an empty list.
2. Copy digits exactly as they appear. Do not complete missing digits, do not correct spelling, do not reformat or reorder numbers, and keep leading zeros.
3. A sticker or stamp that is cut off, smudged or partly covered: set legible to false and copy the partial value exactly as it is seen (null if nothing can be read).
4. "דרישה" and "דגימה" are names of the commitment number field (commitment_numbers).
5. Return every central number ("מרכזי"): one item per sticker, and one per number handwritten in a sticker's place, even if there are several.
6. Return every commitment number: one item per number. When the form carries several commitments, each one is a separate item.
7. Never read a number from the bars of a barcode (only digits printed as text count), and never fill in a missing part of a date. Copy received_date as it is written (e.g. 01.02.2025); if the year is not written, copy what is written and add a note.

## For every value
- value: the text exactly as written, or null.
- bbox: where the value itself is (the digits or the words, not their label), as fractions from 0 to 1 of the image width and height: x0,y0 is the top-left corner and x1,y1 the bottom-right corner. page is the 0-based index of the image the value is on. null when value is null and nothing is visible.
- legible: false if the value is cut off, smudged, covered or otherwise not fully readable; otherwise true.
- source: printed, handwritten, sticker or stamp.
- note: a few words only when something is unusual (e.g. "sticker cut at the right edge", "year not written"), otherwise null. Never copy names or numbers into a note.

## This form's layout
{{form_type}}
