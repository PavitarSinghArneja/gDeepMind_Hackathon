"""Check that Ollama and the Gemma 4 models are ready, and time one real call per model.
Run: python scripts/smoke_models.py   (after scripts/make_mock_data.py)"""
from __future__ import annotations

import sys
import time

from snapsort.agent import prompts
from snapsort.agent.schemas import TRIAGE_SCHEMA
from snapsort.config import Settings
from snapsort.llm import LLMError, OllamaLLM, installed
from snapsort.sense.extract import extract


def main() -> None:
    s = Settings()
    llm = OllamaLLM(s.ollama_url, s.embed_model, s.llm_timeout_s)
    try:
        names = llm.models()
    except LLMError as e:
        sys.exit(f"Ollama isn't reachable at {s.ollama_url}: {e}\nStart it with: ollama serve")
    missing = [m for m in (s.triage_model, s.work_model, s.embed_model) if not installed(names, m)]
    if missing:
        sys.exit(f"Missing models: {missing}\nInstalled: {names}\nRun `ollama pull <name>` or set SNAPSORT_*_MODEL.")
    sample = next((s.root / "mock" / "Downloads").glob("Airtel_Bill_*.pdf"), None)
    if sample is None:
        sys.exit("No mock data. Run: python scripts/make_mock_data.py")
    content = extract(sample)
    for model in (s.triage_model, s.work_model):
        t = time.time()
        out = llm.generate_json(model, prompts.triage_prompt(sample.name, content.text), TRIAGE_SCHEMA, content.images[:1])
        print(f"{model:24} {time.time() - t:5.1f}s  images={'yes' if llm.vision else 'no (text only)'}  {out}")
    t = time.time()
    dims = len(llm.embed("wifi password"))
    print(f"{s.embed_model:24} {time.time() - t:5.1f}s  {dims} dimensions")


if __name__ == "__main__":
    main()
