# Edge cases actually observed in samples/ (Phase 0)

Every item was observed on at least one page (`P1 p5` = part 1, page 5). Theoretical cases are not included.
Quoted Hebrew is the literal text printed on the form.

## Scanning and layout
1. **No text layer** — all 48 pages are image-only, so OCR is always required.
2. **Rotated pages** — 90°/270° (P1 p3,11,14,22,23,25; P2 p14,15,20–22). The PDF `/Rotate` field is 0 on all of them, so orientation must be detected from the image.
3. **Vertical line across the whole page** — a scanner streak runs through almost every page, sometimes through digits.
4. **Fax quality** — dotted, faded print (P2 p6,7, Barzilai) and dot-matrix printing (P1 p9).
5. **Printed screenshot** (Sheba) — small screen font, blurry on some pages (P2 p20–22).
6. **Number cut off at the scan margin** — a commitment number whose first digits were cut off (P2 p21). It must be flagged, not completed.
7. **Non-uniform page size** — most pages are 593–606 × 842–884 pt.

## Central number
8. **Several central numbers on one form** — 2 (P1 p14,24; P2 p12) and 3 (P1 p9).
9. **Handwritten central number without a sticker**, sometimes with the words "מספר מרכזי" and sometimes without (all Sheba screenshots, Maccabi ONLINE, Rambam fax).
10. **Central sticker in different orientations** — the word "מרכזי" is printed vertically next to the digits, and appears upside down when the page is rotated.
11. **Central sticker covers other text** — e.g. the Leumit logo.
12. **Other 6-digit numbers nearby** — e.g. "מספר בקשה" at Shaare Zedek, or the number above the barcode at Bnei Zion. Detection must therefore be anchored on the word "מרכזי".

## Received date
13. **Faded or almost invisible "התקבל" stamp**, where only the bold date is readable (P1 p17,20; P2 p3,5,18).
14. **Tilted or rotated stamp** (P1 p8; P2 p1,15,16).
15. **No stamp, handwritten date next to the central number** — in formats like `16/6/26`, `13.8.26`, `-31.8` (no year!).
16. **Several competing dates** — the form date, the collection date, a "נשלח בתאריך" (sent on) stamp (Schneider), and a handwritten date that differs from the stamp (Barzilai: a stamp with one date and handwriting with another day).
17. **Wrong or missing year on the form** — "06/08/206" (P1 p16), and 2024 dates on a form received in 2026 (P1 p24).
18. **Stamp with spaced-out digits** — `1 8 -06- 2026`.

## Commitment number
19. **Several commitment numbers** — 2–3 rows in the Sheba table (P1 p25; P2 p20), and 2 different numbers at Wolfson ("מס'" and a sticker).
20. **Same number several times** — Maccabi ONLINE ×3, IDF ×4, Shaare Zedek ×3. Needs dedupe.
21. **Number in the wrong field** — on form 293/17 the number is handwritten in the "האבחנה" (diagnosis) field and the "מספר ההתחייבות" box is empty.
22. **Handwritten commitment number with a strike-through** (Barzilai).
23. **Number hidden by a stamp** (BIOHAZARD at Wolfson).
24. **Leading zeros** — `000302...`; must be stored as a string.
25. **Varying lengths** — 4 digits (Shamir), 5, 6, 7, 9 and 12 (Sheba).
26. **No commitment number at all** — 9 types (see form_mapping.md). The field stays empty and flagged.
27. **Competing numbers** — "מספר הפניה" (referral number) vs. "אסמכתא" (reference) (IDF), a 10-digit barcode vs. "התחייבות מספר" (Meuhedet), test-tube suffixes `-(713)` (Maccabi).

## Name and ID number
28. **Name as a single field** ("שם", "שם ומשפחה", "מטופל"). Must be split into first and last name, and flagged for review when the split is ambiguous.
29. **Handwritten name and ID number** — in the samples all of them were replaced with blue handwriting for anonymization, sometimes with scribbles over the original (P1 p14,22).
30. **ID numbers of different lengths** — 8 or 9 digits, sometimes with leading zeros, and the samples also include IDs with a wrong check digit. This is a soft signal only (principle 4).
31. **Trap ID number** — the Clalit Form 17 shows, at the bottom, the ID number of the person who printed the form.
32. **ID number appears several times**, both as a barcode and on a sticker, which can be used as a confirming signal.

## Sender
33. **Senders that are neither an HMO nor a hospital** — the Ministry of Defense, the Palestinian Authority, medical tourism, the IDF, forensic medicine, a private lab (Rosenblatt).
34. **"ללא קופ"ח" (no HMO)** — on an admission sticker (Wolfson) and at Sheba.
35. **The sending hospital differs from the paying HMO** — Schneider with "גורם ממנן: מאוחדת", and Rambam with "קופ"ח: כללית".
36. **The form's "גורם שולח" is a clinic or department, not the paying body** (Leumit, LabOS).

## Form separation
37. **2-page form** (Maccabi ONLINE "1/2", Assuta "Page 1 of 2"). Only the first page is in the samples.
38. **Text stamps** such as "נסיוב" (serum), "דם מלא" (whole blood), "שתן" (urine), "צואה" (stool), "CSF", "נסרק" (scanned), "מקור" (original). They are not fields and must be ignored.
