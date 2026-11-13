"""Derleme sirasinda (GitHub Actions'ta) modelleri indirip hedef klasore koyar."""
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

# 1) Uyandirma kelimesi icin kucuk Ingilizce Vosk modeli ("hey tank")
name = "vosk-model-small-en-us-0.15"
tmp = Path(tempfile.mkdtemp())
print("Vosk indiriliyor...")
urllib.request.urlretrieve(f"https://alphacephei.com/vosk/models/{name}.zip", tmp / "v.zip")
with zipfile.ZipFile(tmp / "v.zip") as z:
    z.extractall(tmp)
shutil.move(str(tmp / name), str(out / "vosk-wake"))

# 2) Turkce komutlar icin faster-whisper modeli
from huggingface_hub import snapshot_download

repo = os.environ.get("WHISPER_REPO", "Systran/faster-whisper-small")
print("Whisper indiriliyor:", repo)
snapshot_download(
    repo,
    local_dir=str(out / "whisper"),
    allow_patterns=["config.json", "model.bin", "tokenizer.json",
                    "vocabulary.*", "preprocessor_config.json"],
)
shutil.rmtree(out / "whisper" / ".cache", ignore_errors=True)
print("Tamam.")
