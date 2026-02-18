"""Download the MiniLM ONNX model and tokenizer from HuggingFace."""

from __future__ import annotations

import sys
from pathlib import Path


def download_model(output_dir: str = ".") -> None:
    """Download all-MiniLM-L6-v2 ONNX model and tokenizer."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("Installing huggingface_hub...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "huggingface-hub"])
        from huggingface_hub import hf_hub_download

    out = Path(output_dir)

    # Download ONNX model
    print("Downloading MiniLM ONNX model...")
    model_path = hf_hub_download(
        repo_id="sentence-transformers/all-MiniLM-L6-v2",
        filename="onnx/model.onnx",
        local_dir=str(out / "_hf_cache"),
    )
    # Copy to output
    import shutil
    dest_model = out / "minilm.onnx"
    shutil.copy2(model_path, dest_model)
    print(f"  -> {dest_model}")

    # Download tokenizer
    print("Downloading tokenizer...")
    tokenizer_path = hf_hub_download(
        repo_id="sentence-transformers/all-MiniLM-L6-v2",
        filename="tokenizer.json",
        local_dir=str(out / "_hf_cache"),
    )
    dest_tokenizer = out / "tokenizer.json"
    shutil.copy2(tokenizer_path, dest_tokenizer)
    print(f"  -> {dest_tokenizer}")

    # Cleanup cache
    cache_dir = out / "_hf_cache"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)

    print("Done! Model and tokenizer ready.")


if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else "."
    download_model(output)
