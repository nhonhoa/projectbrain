from pbrain.text import remove_accents, words


def test_remove_accents_strips_diacritics_and_punctuation():
    assert remove_accents("Tiếng Việt, Đường!") == "tieng viet duong"
    assert remove_accents("") == ""
    assert remove_accents(None) == ""


def test_words_splits_camel_snake_and_accents():
    assert words("parseDateNow_x") == ["parse", "date", "now", "x"]
    assert words("Định dạng") == ["dinh", "dang"]
    assert words(None) == []
