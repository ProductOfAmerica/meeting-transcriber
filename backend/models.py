"""Pinned speech-model files: exact repo revision and sha256 for every file.

Import-safe. `fetch(root)` downloads each model into a temporary sibling
folder, verifies every file, then renames the folder into place, so a model
folder that exists is complete. The file lists are the files onnx-asr's
loader opens for these models in fp32.

Run from source (developers): python -m backend.models [models_dir]
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from . import fetch as _fetch
from . import fsutil


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo: str
    revision: str
    files: tuple            # ((filename, sha256), ...)

    def dir(self, root) -> Path:
        return Path(root) / f"{self.name}-{self.revision[:12]}"

    def url(self, filename: str) -> str:
        return (f"https://huggingface.co/{self.repo}/resolve/"
                f"{self.revision}/{filename}")


PARAKEET = ModelSpec(
    name="parakeet-tdt-0.6b-v2",
    repo="istupakov/parakeet-tdt-0.6b-v2-onnx",       # CC-BY-4.0
    revision="0bbb45a3365852604aef28b538a8f066f4ccaa85",
    files=(
        ("config.json",
         "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466"),
        ("vocab.txt",
         "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d"),
        ("decoder_joint-model.onnx",
         "cbb52a07bd70ab5b67f8439d4b3cd8704b18467b4430bcacb5adabe154b8d191"),
        ("encoder-model.onnx",
         "3987bcd28175d829d12888a996a84e8f62a0e374d9ffd640662c1515adc679d3"),
        ("encoder-model.onnx.data",
         "4dab7362d4874d85965045b1e41b2d61dd2cc0fb25671a7f6b3dc47bf120cc41"),
    ))

SILERO = ModelSpec(
    name="silero-vad",
    repo="istupakov/silero-vad-onnx",                 # MIT
    revision="b3e3ee3cce4c11ceb63b1a0b229d916069c1ddf6",
    files=(
        ("config.json",
         "1094039d370c82889582ba739a3d1caac5754c8b3a17a66a534200c9f72086e2"),
        ("silero_vad.onnx",
         "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"),
    ))

ALL = (PARAKEET, SILERO)


def present(root) -> bool:
    return all(spec.dir(root).is_dir() for spec in ALL)


def fetch(root, *, on_file=None, on_pct=None, cancelled=None) -> None:
    """Download every missing model under root. on_file(spec, filename) runs
    before each file; on_pct(0-100) reports that file's progress;
    cancelled() -> bool stops the download."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for spec in ALL:
        final = spec.dir(root)
        if final.is_dir():
            continue
        tmp = root / f".{final.name}.partial"
        fsutil.rmtree(tmp)
        for filename, sha in spec.files:
            if on_file:
                on_file(spec, filename)
            _fetch.download(spec.url(filename), tmp / filename, sha,
                            on_pct, cancelled)
        fsutil.move_into_place(tmp, final)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = (Path(argv[0]) if argv
            else Path(__file__).resolve().parent.parent / "models")

    def on_file(spec, filename):
        print(f"{spec.name}: {filename}", flush=True)

    fetch(root, on_file=on_file)
    print(f"models ready in {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
