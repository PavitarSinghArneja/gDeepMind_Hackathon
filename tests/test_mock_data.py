from datetime import date

import fitz

from scripts.make_mock_data import generate


def test_generates_expected_set(tmp_path):
    root = tmp_path / "mock"
    files = generate(root, today=date(2026, 9, 26))
    names = {p.name for p in files}
    assert len(files) == 26
    for name in ("Airtel_Bill_Sep2026.pdf", "document(1).pdf", "Statement_Protected.pdf", "ReadMe_Assistant.txt",
                 "IMG_ID_card_front.png", "recipe_notes.txt", "Airtel_Bill_Oct2026.pdf"):
        assert name in names
    dl = root / "Downloads"
    assert (dl / "document(1).pdf").read_bytes() == (dl / "Airtel_Bill_Sep2026.pdf").read_bytes()
    with fitz.open(dl / "Airtel_Bill_Sep2026.pdf") as d:
        assert "05 Oct 2026" in d[0].get_text()
    with fitz.open(dl / "Statement_Protected.pdf") as d:
        assert d.needs_pass
    with fitz.open(dl / "Scanned_Rent_Agreement.pdf") as d:
        assert d[0].get_text().strip() == ""


def test_regenerating_wipes_old_files(tmp_path):
    root = tmp_path / "mock"
    generate(root, today=date(2026, 9, 26))
    (root / "Downloads" / "stray.txt").write_text("x")
    generate(root, today=date(2026, 9, 26))
    assert not (root / "Downloads" / "stray.txt").exists()
