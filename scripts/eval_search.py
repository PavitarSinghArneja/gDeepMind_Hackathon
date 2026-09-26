"""Compare search with keywords only vs keywords + EmbeddingGemma on questions with a known right file.
Run after a full `python -m snapsort --headless`:  PYTHONPATH=. python scripts/eval_search.py"""
from __future__ import annotations

from snapsort import db, search
from snapsort.config import Settings
from snapsort.llm import LLMError, OllamaLLM

# question -> a substring of the right file's original name
CASES = {
    "how much is my internet bill": "Airtel_Bill",
    "router login details": "21.14.03",
    "blood sugar test results": "Apollo_Diagnostics",
    "what medicines was I told to take": "Rx_Dr_Rao",
    "my health cover card": "StarHealth",
    "train journey to bangalore": "IRCTC",
    "how much did I earn last month": "Payslip",
    "coffee I paid for with UPI": "13.02.44",
    "house lease terms": "Scanned_Rent_Agreement",
    "one time code from the bank": "09.41.12",
}


class KeywordOnly:
    """Same model, but embeddings switched off, to measure what vectors add."""

    def __init__(self, llm):
        self.llm = llm

    def embed(self, text):
        raise LLMError("embeddings disabled for comparison")


def rank_of(conn, llm, question, needle) -> int | None:
    for i, row in enumerate(search.search(conn, llm, question, k=5), 1):
        if needle in row["original_path"]:
            return i
    return None


def main() -> None:
    s = Settings()
    conn = db.connect(s.db_path)
    llm = OllamaLLM(s.ollama_url, s.embed_model, 120)
    vectors = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    print(f"files with vectors: {vectors}\n")
    print(f"{'question':42} {'keywords':>9} {'+ vectors':>10}")
    hits = {"kw": 0, "hy": 0}
    for q, needle in CASES.items():
        kw = rank_of(conn, KeywordOnly(llm), q, needle)
        hy = rank_of(conn, llm, q, needle)
        hits["kw"] += kw == 1
        hits["hy"] += hy == 1
        show = lambda r: f"#{r}" if r else "miss"
        print(f"{q:42} {show(kw):>9} {show(hy):>10}")
    print(f"\ncorrect file ranked first: keywords {hits['kw']}/{len(CASES)}, keywords + vectors {hits['hy']}/{len(CASES)}")


if __name__ == "__main__":
    main()
