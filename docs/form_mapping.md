# Form type mapping — Phase 0

Source: `samples/` — 2 PDF files, 48 pages (part 1: 26 pages, part 2: 22 pages). All pages
are scans (NAPS2/PDFsharp) with no text layer, so OCR is required on every page.
Every page was reviewed visually at 150 DPI, and rotated scans were also reviewed after rotation.

> No real field values appear in this document. In the samples the name and ID number were replaced
> with blue handwriting (anonymization), and the table describes **location and format** only.

Page references: `P1 p5` = part 1, page 5. Quoted Hebrew is the literal text printed on the form.

**Fields shared by all types:**
- **Central number** — a 6-digit barcode sticker with the word "מרכזי" next to it (printed vertically or horizontally), usually highlighted in yellow. On the Sheba screenshots, and on some faxes, there is no sticker and the number is handwritten.
- **Received date** — a rectangular stamp "התקבל / המעבדה המרכזית לנגיפים / המרכז הרפואי המשולב" with a `DD -MM- YYYY` date. When there is no stamp, the date is handwritten below or next to the central number.

**Certainty:** `certain` = the layout and the location of the commitment number are clear. `uncertain` = no explicit commitment field was found, or we saw only one sample with numbers whose meaning is unclear.

| ID (YAML) | Certainty | Detection (logo / title) | Name and ID number | Sender | Commitment number (kind) | Typical pages | Observed edge cases | Samples |
|---|---|---|---|---|---|---|---|---|
| `meuhedet_form17` | certain | "מאוחדת" logo; "התחייבות לתשלום עבור שירותי בריאות" | "פרטי המבוטח/ת" table: a single full name, ID number | Meuhedet (the HMO) | **Request/commitment**: "התחייבות מספר" + 9 digits. The 10-digit barcode is not the number | 1 | "התקבל" stamp next to a round stamp; "מקור" | P1 p1 |
| `clalit_form17` | certain | "כללית" logo (twice); "טופס 17" | "מטופל:" row (name + ID together) | Clalit | **Commitment**: "...לטיפולים אמבולטוריים מספר:" + 9 digits | 1 | The ID number of whoever printed the form appears at the bottom — an ID trap | P1 p5 |
| `maccabi_online` | certain | "מכבי" logo; maccabi4u; "ONLINE" | "שם:" + "ת.ז.:" on the top row; ID also as a barcode | Maccabi | **Commitment**: 9 digits ×3 (title, barcode, appendix). Same number, needs dedupe | 2 ("1/2") | Handwritten central number + date, "נסרק" stamp; page 2 missing from the sample | P1 p10 |
| `sheba_insurance_screen` | certain | Screenshot: "קשרי ביטוח - סקירה", "נתוני ביקור" | Top row: date of birth + ID + name; repeated in the insured person's row | "שם מבטח" / "גורם מפנה" column: HMOs, **Ministry of Defense**, **Palestinian Authority**, **medical tourism**, "ללא קופ"ח" (no HMO) | **Commitment**: "מספר התחייבות" column, 1–3 rows (varying lengths: 7–12 digits, including leading zeros) | 1 | No central sticker: handwritten central number + date; scan rotated 90°/270°; number cut off at the margin (P2 p21) | P1 p2,3,4,7,11,25; P2 p20–22 |
| `labos_result_report` | certain | "דוח תוצאות", ClabOS; Clalit + lab logo | Header row: last name / first name / ID | "גורם שולח" (code + department + hospital); the paying HMO is Clalit | **Sample**: "מס' הדגימה". According to the stamp, the sample number serves as the commitment reference | 1 | At Carmel there is no "התקבל" stamp, only a handwritten date | P1 p6,12 |
| `leumit_lab_worklist` | certain | "דוח רשימת עבודה לפי בדיקה"; Leumit logo | "שם ומשפחה:" (one field), "ת.ז.:" | Leumit (stamp). The form's "גורם שולח" is a clinic | **Sample**: "דגימה:" + 9 digits | 1 | Logo covered by the central sticker | P1 p8 |
| `maccabi_lab_request` | certain | Dot-matrix print; "קופ"ח מכבי-מעבדה שולחת" | "נבדק:", "ת.ז.:" | Maccabi | **Request**: "דרישה: branch/8 digits" + a "דרישה:" sticker. Test-tube stickers with a `-(713)` suffix contain the same number | 1 | **3 central stickers** | P1 p9 |
| `shaare_zedek_external` | certain | Shaare Zedek logo; "בקשה לבדיקת מעבדה מחוץ למרכז הרפואי" | "שם פרטי/שם משפחה/מספר זהות", and also a sticker | Shaare Zedek (the "קופת חולים" field is empty) | **Commitment**: 6 digits ×3 ("מספר בקשה" = "מספר התחייבות") | 1 | No "התקבל" stamp; the bold date is the commitment date | P1 p13 |
| `rambam_external` | certain | Rambam tree logo; "טופס לשליחת בדיקה לגורמי חוץ" | "פרטי הנבדק", and also the bottom sticker | Rambam (the sticker also shows the HMO) | **Sample**: "מספר דגימה", 9 digits. Declared as the commitment number | 1 | Fax version: handwritten central number + date; wrong year on the form ("06/08/206") | P1 p15,16,19 |
| `idf_meditik` | certain | Medical Corps emblem; "MediTik"; "אסמכתא תקציבית" | "פרטי המטופל" table | IDF | **Other — budget reference**, 9 digits (×4). "מספר הפניה" is a different number | 1 | "התקבל" stamp almost transparent | P1 p20 |
| `shamir_external` | certain | "המרכז הרפואי שמיר" logo; "טופס התחייבות לבדיקת חוץ" | Admission sticker | Shamir (hospital); HMO on the sticker | **Commitment**: printed box, 4 digits | 1 | Handwritten notes overlapping fields; tilted stamp | P1 p26; P2 p1 |
| `wolfson_external` | certain | "ע"ש אדית וולפסון"; "טופס התחייבות להזמנת בדיקות חוץ" | Admission sticker | Wolfson; the sticker may also say "ללא קופ"ח" | **Commitment**: "מס'" 5 digits (hidden by a BIOHAZARD stamp in one sample). A vertical 7-digit sticker: uncertain | 1 | **2 central stickers**; stamp hides the number | P2 p12,13 |
| `clalit_hospital_293_17` | certain | Clalit logo; "בקשה לבדיקת מעבדה מחוץ לבית החולים"; "293/17" | "שם החולה" in numbered boxes | Hospital handwritten ("השרון"); Clalit | **Commitment**: the box (51–54) is empty, **and the number is handwritten in the diagnosis field** | 1 | Number in the wrong field | P2 p10,11 |
| `meir_external` | certain | "מרכז רפואי מאיר" + Clalit; catalog no. 81054 | Sticker only (boxes empty) | Meir | **Commitment**: the box is empty. A 5-digit sticker on the logo: uncertain | 1 | Sticker covers the title | P2 p16,17 |
| `bnei_zion_external` | certain | "בני ציון" logo; "מסמך זה מהווה התחייבות כספית" | Admission sticker | Bnei Zion; HMO on the sticker | **Other**: 6-digit number above a barcode. Assumed to be the form number, uncertain | 1 | Faded "התקבל" stamp; handwritten date | P2 p4,5 |
| `barzilai_letter` | certain | "ברזילי" logo; "לכבוד" letter | Admission sticker | Barzilai; HMO on the sticker | **Commitment**: "מס' התחייבות" **handwritten**, 5 digits | 1 | Fax quality (dotted); strike-through inside the number; two different dates | P2 p6,7 |
| `tzafon_letter` | **uncertain** | "צפון" logo, "ע"ש ברוך פדה" | Handwritten, with strike-throughs | Tzafon (Poriya) | **Not found** | 1 | Scan rotated 90°; **2 central stickers** | P1 p14 |
| `rosenblatt_lab` | **uncertain** | "רוזנבלט מעבדה רפואית בע"מ" | ROSENBLATT sticker | Private lab (is it in the closed list?) | **Not found** (4-digit number on the sticker: maybe "מס' קליטה", intake number) | 1 | Stamp very faded or missing | P1 p17,18,21 |
| `shmuel_harofe_letter` | **uncertain** | "שמואל-הרופא", "התחייבות כספית" | Handwritten | Shmuel Harofe | **Not found** ("מס' גליון" empty) | 1 | Rotated scan; no "התקבל" stamp | P1 p22,23 |
| `forensic_medicine` | **uncertain** | "המרכז הלאומי לרפואה משפטית" | Printed fields | Government body | **Not found** ("מס' תיק", case number: uncertain) | 1 | **2 central stickers**; 2024 dates on the form vs. received in 2026 | P1 p24 |
| `schneider_request` | **uncertain** | "שניידר" logo + Clalit | Printed fields | Schneider hospital; "גורם ממנן" (funding party) = a different HMO (Meuhedet) | **Not found** ("מס' ביקור", and a bold number below the barcode: uncertain) | 1 | An extra "נשלח בתאריך" (sent on) stamp competes with "התקבל" | P2 p2 |
| `assuta_ashdod_external` | **uncertain** | "אסותא אשדוד"; FOR-0000189 | Admission sticker | Assuta Ashdod | **Not found** ("מספר מבחנה/מיכל", tube/container number: uncertain) | 2 ("Page 1 of 2") | Page 2 not seen | P2 p3 |
| `hillel_yaffe_lab` | **uncertain** | "הלל יפה" logo; "מעבדה" | Admission sticker | Hillel Yaffe | **Not found** (barcode "2648 xxxxx": uncertain) | 1 | One sample has no stamp | P2 p8,9 |
| `laniado_request` | **uncertain** | "לניאדו" logo | Small sticker | Laniado; HMO on the sticker | **Not found** (sticker "X" + 6 digits: uncertain) | 1 | Scan rotated 90°; gray background | P2 p14,15 |
| `kaplan_external` | **uncertain** | "קפלן" logo + "כללית 100" | Admission sticker | Kaplan; HMO on the sticker | **Not found** (8-digit barcode: uncertain) | 1 | Weak "התקבל" stamp; handwritten notes | P2 p18,19 |

Total: 25 types — 16 `certain` and 9 `uncertain`. All 48 pages were assigned a type. Every page is a single form, except `maccabi_online`, which is 2 pages, and only its first page is in the samples.

**How the commitment number appears — summary:**
- **Request ("דרישה")**: Maccabi — "דרישה:" (`maccabi_lab_request`) and "מספר התחייבות" (`maccabi_online`).
- **Sample ("דגימה")**: Leumit ("דגימה:"), LabOS ("מס' הדגימה"), Rambam ("מספר דגימה").
- **Explicit commitment**: Meuhedet, Clalit Form 17, Shaare Zedek, Shamir, Wolfson, Barzilai (handwritten), Sheba (table).
- **Other**: IDF ("אסמכתא תקציבית", budget reference).
- **Not on the form**: the 9 `uncertain` types, and also 293/17 and Meir, where the box is empty.
