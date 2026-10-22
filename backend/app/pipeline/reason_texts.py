"""Hebrew text for every reason code, shown to the reviewer next to a field.

``Field.reasons`` keeps the machine codes (later stages and tests read them);
``explain`` turns them into the readable lines stored in
``Field.explanations``. The texts are exported verbatim, so they are in
Hebrew; they describe the check, never the value.
"""
from __future__ import annotations

from app.pipeline import classify, extract, normalize, ocr_check, reread
from app.schemas import NOT_FOUND, OPTIONAL_MISSING

SINGLE_READING = "single_reading"  # set by the confidence stage: no independent reading confirmed the value

REASON_TEXTS: dict[str, str] = {
    NOT_FOUND: "השדה לא נמצא בטופס",
    OPTIONAL_MISSING: "השדה ריק, ובסוג טופס זה הוא אינו חובה",
    SINGLE_READING: "נקרא פעם אחת בלבד, בלי אישור ממקור נוסף",
    extract.ILLEGIBLE: "הערך אינו קריא במלואו (חתוך, מטושטש או מוסתר)",
    extract.HANDWRITTEN: "כתב יד",
    extract.BBOX_IMPLAUSIBLE: "מיקום הערך בדף לא זוהה, ולכן לא נבדק שוב",
    normalize.UNPARSED_DATE: "התאריך לא זוהה כתאריך",
    normalize.ID_NOT_DIGITS: "בת.ז. יש תווים שאינם ספרות",
    normalize.ID_PADDED: "נוספו אפסים מובילים לת.ז.",
    normalize.ID_LENGTH_INVALID: "אורך הת.ז. אינו 9 ספרות",
    normalize.ID_CHECKSUM_FAILED: "ספרת הביקורת של הת.ז. אינה תקינה",
    normalize.CENTRAL_NOT_DIGITS: "במספר המרכזי יש תווים שאינם ספרות",
    normalize.DATE_YEAR_MISSING: "בתאריך חסרה שנה",
    normalize.DATE_TWO_DIGIT_YEAR: "השנה נכתבה בשתי ספרות והושלמה ל-20xx",
    normalize.DATE_DAY_MONTH_SWAPPED: "ייתכן שהיום והחודש הוחלפו",
    normalize.DATE_INVALID: "תאריך שאינו קיים",
    normalize.DATE_IN_FUTURE: "התאריך מאוחר מהיום",
    normalize.DATE_BEFORE_WINDOW: "התאריך מוקדם מ-2000",
    normalize.COMMITMENT_PATTERN_MISMATCH: "מבנה מספר ההתחייבות שונה מהמקובל בסוג טופס זה",
    ocr_check.OCR_AGREES: "זיהוי התווים (OCR) אישר את הקריאה",
    ocr_check.OCR_DISAGREES: "הערכים נבדלו בין קריאת המודל לזיהוי התווים (OCR)",
    ocr_check.OCR_EMPTY: "זיהוי התווים (OCR) לא קרא ספרות",
    ocr_check.OCR_UNAVAILABLE: "לא ניתן היה לבדוק בזיהוי תווים (OCR)",
    reread.REREAD_AGREES: "קריאה שנייה אישרה את הערך",
    reread.REREAD_DISAGREES: "הערכים נבדלו בין שתי הקריאות",
    reread.REREAD_NULL: "בקריאה השנייה לא נקרא ערך",
    reread.REREAD_FOUND_VALUE: "בקריאה השנייה נמצא ערך שלא נקרא בראשונה (לא הוזן)",
    reread.REREAD_UNAVAILABLE: "לא ניתן היה לקרוא שנית (אין מיקום בדף)",
    reread.REREAD_FAILED: "הקריאה השנייה נכשלה",
    reread.REREAD_LIMIT: "לא נקרא שנית (הגעה למכסת הקריאות לטופס)",
    classify.SENDER_NOT_IN_LIST: "הגורם השולח אינו ברשימה הסגורה",
    classify.SENDER_AMBIGUOUS: "הגורם השולח מתאים ליותר מגורם אחד ברשימה",
    classify.SENDER_FUZZY_MATCH: "הגורם השולח הותאם לרשימה בהתאמה חלקית",
    classify.SENDER_READINGS_AGREE: "שתי קריאות של הגורם השולח הסכימו",
    classify.SENDER_READINGS_DISAGREE: "הגורם השולח נבדל בין שתי הקריאות",
}

_CANDIDATE_TEXT = "מועמד אפשרי: {name} (התאמה {score})"


def explain(reasons: list[str]) -> list[str]:
    """The Hebrew line for each reason code, in order. A sender candidate
    names the listed sender (an organization, not patient data); an unknown
    code is shown as it is rather than dropped."""
    return [_explain_one(reason) for reason in reasons]


def _explain_one(reason: str) -> str:
    if reason.startswith(classify.SENDER_CANDIDATE_PREFIX):
        name, _, score = reason.removeprefix(classify.SENDER_CANDIDATE_PREFIX).rpartition("=")
        return _CANDIDATE_TEXT.format(name=name, score=score)
    return REASON_TEXTS.get(reason, reason)
