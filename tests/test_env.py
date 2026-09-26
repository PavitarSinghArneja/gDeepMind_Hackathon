import sqlite3


def test_sqlite_has_fts5():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")


def test_tesseract_available():
    import pytesseract

    assert pytesseract.get_tesseract_version()
