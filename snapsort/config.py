"""Paths, model names and thresholds. Everything lives inside this repo folder."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    ollama_url: str = field(default_factory=lambda: _env("SNAPSORT_OLLAMA_URL", "http://127.0.0.1:11434"))
    triage_model: str = field(default_factory=lambda: _env("SNAPSORT_TRIAGE_MODEL", "gemma4:e2b"))
    work_model: str = field(default_factory=lambda: _env("SNAPSORT_WORK_MODEL", "gemma4:e4b"))
    embed_model: str = field(default_factory=lambda: _env("SNAPSORT_EMBED_MODEL", "embeddinggemma"))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("SNAPSORT_LLM_TIMEOUT", "180")))
    port: int = field(default_factory=lambda: int(_env("SNAPSORT_PORT", "8765")))
    lease_seconds: int = 300
    max_attempts: int = 3

    @property
    def watch_dirs(self) -> tuple[Path, ...]:
        return tuple(self.root / "mock" / name for name in ("Downloads", "Screenshots", "Desktop"))

    @property
    def library_dir(self) -> Path:
        return self.root / "library"

    @property
    def vault_dir(self) -> Path:
        return self.root / "vault"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "snapsort.db"

    @property
    def vault_key_path(self) -> Path:
        return self.data_dir / "vault.key"

    @property
    def policy_path(self) -> Path:
        return ROOT / "config" / "policy.yaml"

    def ensure_dirs(self) -> None:
        for d in (*self.watch_dirs, self.library_dir, self.vault_dir, self.data_dir):
            d.mkdir(parents=True, exist_ok=True)
