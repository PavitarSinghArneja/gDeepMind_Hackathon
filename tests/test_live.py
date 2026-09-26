"""Real Gemma 4 via Ollama. Run with: pytest -m live -v"""
import shutil

import pytest

from snapsort.agent.pipeline import Pipeline
from snapsort.agent.policy import load_policy
from snapsort.llm import OllamaLLM
from snapsort.sense.watcher import register_file

pytestmark = pytest.mark.live


@pytest.mark.parametrize("pattern,doc_type", [
    ("Downloads/Airtel_Bill_*.pdf", "bill"),
    ("Screenshot * at 21.14.03.png", "credential"),
    ("Apollo_Diagnostics_Report.pdf", "lab_report"),
])
def test_real_models_classify_mock_files(conn, settings, mock_dir, pattern, doc_type):
    llm = OllamaLLM(settings.ollama_url, settings.embed_model, settings.llm_timeout_s)
    src = next(mock_dir.rglob(pattern))
    dest = settings.watch_dirs[0] / src.name
    shutil.copyfile(src, dest)
    fid = register_file(conn, settings, dest)
    Pipeline(conn, settings, llm, load_policy(settings.policy_path)).run_once()
    row = conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    assert row["doc_type"] == doc_type, (row["title"], row["fields_json"])
    assert row["status"] in ("done", "needs_human")
