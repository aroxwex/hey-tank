"""Derleme sirasinda (GitHub Actions) modelleri indirip hedef klasore koyar."""
import os, shutil, sys, tempfile, urllib.request, zipfile
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)


def vosk(name, dest):
    tmp = Path(tempfile.mkdtemp())
    print("Vosk indiriliyor:", name)
    urllib.request.urlretrieve(f"https://alphacephei.com/vosk/models/{name}.zip", tmp / "m.zip")
    with zipfile.ZipFile(tmp / "m.zip") as z:
        z.extractall(tmp)
    shutil.move(str(tmp / name), str(out / dest))


vosk("vosk-model-small-tr-0.3", "vosk-tr")
vosk("vosk-model-small-en-us-0.15", "vosk-en")

from huggingface_hub import snapshot_download

repo = os.environ.get("WHISPER_REPO", "Systran/faster-whisper-small")
print("Whisper indiriliyor:", repo)
snapshot_download(repo, local_dir=str(out / "whisper"),
                  allow_patterns=["config.json", "model.bin", "tokenizer.json",
                                  "vocabulary.*", "preprocessor_config.json"])
shutil.rmtree(out / "whisper" / ".cache", ignore_errors=True)
print("Tamam.")
