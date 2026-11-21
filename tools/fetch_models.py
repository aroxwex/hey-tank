import shutil, sys, tempfile, urllib.request, zipfile
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
print("Tamam.")
