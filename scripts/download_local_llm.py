"""Скачивание бесплатной локальной LLM в папку models проекта.

Использование:
    .\\.venv\\Scripts\\python.exe scripts\\download_local_llm.py

Скачивается GGUF-версия Vikhr-Qwen-2.5-1.5b-Instruct (Q4_K_M) — русскоязычная
инструктивная модель, которая быстро работает на CPU через llama.cpp и не
требует API-ключей.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import hf_hub_download

DEFAULT_REPO = "Vikhrmodels/Vikhr-Qwen-2.5-1.5B-Instruct-GGUF"
DEFAULT_FILE = "Vikhr-Qwen-2.5-1.5b-Instruct-Q4_K_M.gguf"
DEFAULT_DIR = Path(__file__).resolve().parent.parent / "models" / "Vikhr-Qwen-2.5-1.5B-GGUF"


def main() -> None:
    parser = argparse.ArgumentParser(description="Скачать локальную LLM для Risk Agent Free")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="Hugging Face repo id")
    parser.add_argument("--file", default=DEFAULT_FILE, help="Имя GGUF-файла")
    parser.add_argument("--dir", default=str(DEFAULT_DIR), help="Куда сохранить модель")
    args = parser.parse_args()

    target = Path(args.dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    print(f"Скачиваю {args.repo}/{args.file} → {target}")
    path = hf_hub_download(
        args.repo,
        args.file,
        local_dir=str(target),
    )
    print(f"Готово: {path}")
    print("Модель доступна Risk Agent Free через llama.cpp.")


if __name__ == "__main__":
    main()
