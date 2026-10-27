from app.hebrew_text import comparable_name, strip_marks, unify_final_letters


def test_whitespace_is_collapsed_and_trimmed():
    assert strip_marks("  בן \t ציון \n") == "בן ציון"


def test_niqqud_and_cantillation_are_removed():
    assert strip_marks("מֹשֶׁה") == "משה"
    assert strip_marks("שָׂרָה֙") == "שרה"  # with a cantillation mark


def test_presentation_forms_become_plain_letters():
    assert strip_marks("שׁבּ") == "שב"  # shin with shin dot, bet with dagesh: one code point each


def test_quote_marks_are_removed():
    assert strip_marks('כ"ץ') == strip_marks("כ״ץ") == strip_marks("כ”ץ") == "כץ"
    assert strip_marks("ז'בוטינסקי") == strip_marks("ז׳בוטינסקי") == "זבוטינסקי"


def test_invisible_characters_are_removed():
    assert strip_marks("‏לוי‎﻿‫") == "לוי"


def test_strip_marks_keeps_final_letters_apart():
    assert strip_marks("אברהם") != strip_marks("אברהמ")


def test_final_letters_are_unified():
    assert unify_final_letters("ךםןףץ") == "כמנפצ"
    assert comparable_name("אברהם") == comparable_name("אברהמ")
    assert comparable_name("הורוביץ") == comparable_name("הורוביצ")


def test_maqaf_and_other_letters_are_kept():
    assert comparable_name("בן־דוד") == "בנ־דוד"
    assert comparable_name("דוד") != comparable_name("רוד")


def test_none_and_blank_give_empty_text():
    assert comparable_name(None) == comparable_name("  ") == ""
