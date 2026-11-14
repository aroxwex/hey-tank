# Hey Tank 1.0 - portable, cevrimdisi Turkce sesli komut asistani (Windows)
import sys
import os

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import json
import re
import gc
import time
import queue
import uuid
import random
import ctypes
import datetime
import threading
from collections import deque
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz

VERSION = "1.0"
SR = 16000


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE = base_dir()
RES = Path(getattr(sys, "_MEIPASS", BASE))
UI_FILE = RES / "ui" / "index.html"
CONFIG_PATH = BASE / "config.json"
COMMANDS_PATH = BASE / "commands.json"
MODELS = BASE / "models"

DEFAULT_CONFIG = {
    "match_threshold": 80,
    "max_record_sec": 7,
    "beep": True,                # "Hey Tank" sonrasi bip: varsayilan ACIK
    "mic_device": None,
    "listening": True,
    "wake_lang": "tr",          # tr | en
    "cmd_engine": "whisper",    # whisper | light
}

WAKE_GRAMMAR = {
    "tr": ["hey tank", "ey tank", "hey tenk", "hey tanık"],
    "en": ["hey tank", "hay tank", "hey thank", "hey tang"],
}
WAKE_TOKENS = {"tank", "tenk", "tanık", "thank", "tang"}


def is_wake(text: str) -> bool:
    return any(t in WAKE_TOKENS for t in text.split())


# ----------------------------------------------------------------------------
# Sistem eylemleri / acma
# ----------------------------------------------------------------------------
SYSTEM_ACTIONS = {
    "lock": "Bilgisayarı kilitle",
    "vol_up": "Sesi aç",
    "vol_down": "Sesi kıs",
    "mute": "Sesi kapat / aç (sessiz)",
    "play_pause": "Müziği durdur / devam ettir",
    "next": "Sonraki şarkı",
    "prev": "Önceki şarkı",
}
VK = {"vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD,
      "play_pause": 0xB3, "next": 0xB0, "prev": 0xB1}


def press_key(vk, times=1):
    u = ctypes.windll.user32
    for _ in range(times):
        u.keybd_event(vk, 0, 0, 0)
        u.keybd_event(vk, 0, 2, 0)
        time.sleep(0.02)


def run_system(name):
    if name == "lock":
        ctypes.windll.user32.LockWorkStation()
    elif name in ("vol_up", "vol_down"):
        press_key(VK[name], 5)
    elif name in VK:
        press_key(VK[name])


def run_command(cmd, log):
    try:
        if cmd.get("system"):
            run_system(cmd["system"])
            log(f"Çalıştı: {SYSTEM_ACTIONS.get(cmd['system'], cmd['system'])}")
            return
        targets = cmd.get("targets") or []
        if not targets:
            log("Bu komutun hedefi yok.")
            return
        chosen = [random.choice(targets)] if cmd.get("mode") == "random" else targets
        for t in chosen:
            t = t.strip().strip('"')
            try:
                if cmd.get("args"):
                    os.startfile(t, arguments=cmd["args"])
                else:
                    os.startfile(t)
                log(f"Açıldı: {t}")
            except Exception as e:
                log(f"Açılamadı ({t}): {e}")
    except Exception as e:
        log(f"Komut hatası: {e!r}")


# ----------------------------------------------------------------------------
# Komutlar
# ----------------------------------------------------------------------------
def _c(name, phrases, targets=None, system=None, mode="all", args="", enabled=True):
    return {"id": uuid.uuid4().hex[:8], "name": name, "phrases": phrases,
            "targets": targets or [], "system": system, "mode": mode,
            "args": args, "enabled": enabled}


def default_commands():
    return [
        _c("YouTube", ["youtube aç", "youtube'u aç"], ["https://www.youtube.com"]),
        _c("Google", ["google aç", "google'ı aç"], ["https://www.google.com"]),
        _c("Spotify", ["spotify aç", "spotify'ı aç"], ["https://open.spotify.com"]),
        _c("Not Defteri", ["not defteri aç", "not defterini aç"], ["notepad.exe"]),
        _c("Hesap Makinesi", ["hesap makinesi aç", "hesap makinesini aç"], ["calc.exe"]),
        _c("Dosya Gezgini", ["dosya gezgini aç", "dosya gezginini aç"], ["explorer.exe"]),
        _c("CS2", ["cs2 aç", "cs2'yi aç", "cs aç", "counter strike aç"], ["steam://rungameid/730"]),
        _c("Bilgisayarı kilitle", ["bilgisayarı kilitle", "ekranı kilitle"], system="lock"),
        _c("Sesi aç", ["sesi aç", "sesi yükselt"], system="vol_up"),
        _c("Sesi kıs", ["sesi kıs", "sesi azalt"], system="vol_down"),
        _c("Sessiz", ["sesi kapat", "sessiz"], system="mute"),
        _c("Müzik durdur/devam", ["müziği durdur", "müziği devam ettir", "müziği duraklat"], system="play_pause"),
        _c("Sonraki şarkı", ["sonraki şarkı", "şarkıyı geç"], system="next"),
        _c("Önceki şarkı", ["önceki şarkı"], system="prev"),
    ]


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


_TR = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def norm(s: str) -> str:
    s = s.replace("İ", "i").replace("I", "ı").lower().translate(_TR)
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def vosk_phrase(p: str) -> str:
    s = p.replace("İ", "i").replace("I", "ı").lower()
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[^a-zçğıöşü0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def best_match(text, commands, threshold):
    t = norm(text)
    if not t:
        return None, 0
    best_cmd, best = None, 0
    for cmd in commands:
        if not cmd.get("enabled", True):
            continue
        for ph in cmd.get("phrases", []):
            p = norm(ph)
            if not p:
                continue
            score = fuzz.ratio(t, p)
            if f" {p} " in f" {t} ":
                score = max(score, 92)
            if score > best:
                best_cmd, best = cmd, score
    return (best_cmd, best) if best >= threshold else (None, best)


# ----------------------------------------------------------------------------
# Windows ile baslat (HKCU\...\Run - yonetici izni gerekmez)
# ----------------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_string():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    return f'"{sys.executable}" "{Path(__file__).resolve()}" --tray'


def get_autostart() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "HeyTank")
            return True
    except Exception:
        return False


def set_autostart(on: bool):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "HeyTank", 0, winreg.REG_SZ, _launch_string())
        else:
            try:
                winreg.DeleteValue(k, "HeyTank")
            except FileNotFoundError:
                pass


# ----------------------------------------------------------------------------
# Cekirdek: ayarlar, komutlar, kayit, durum
# ----------------------------------------------------------------------------
class Core:
    def __init__(self):
        self.cfg = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}
        cmds = load_json(COMMANDS_PATH, None)
        if not isinstance(cmds, list):
            cmds = default_commands()
        for c in cmds:
            c.setdefault("id", uuid.uuid4().hex[:8])
            c.setdefault("enabled", True)
        self.commands = cmds
        save_json(COMMANDS_PATH, self.commands)
        self.lock = threading.Lock()
        self.logs = deque(maxlen=400)
        self.seq = 0
        self.state = {"state": "loading", "status": "Başlatılıyor...", "level": 0.0,
                      "heard": "", "last": None, "beat": time.time()}
        self.engine = Engine(self)

    def log(self, msg):
        with self.lock:
            self.seq += 1
            self.logs.append((self.seq, f"[{datetime.datetime.now():%H:%M:%S}] {msg}"))

    def set_state(self, state, status):
        self.state["state"] = state
        self.state["status"] = status

    def save_commands(self):
        save_json(COMMANDS_PATH, self.commands)

    def save_cfg(self):
        save_json(CONFIG_PATH, self.cfg)

    def handle_text(self, text):
        cmd, score = best_match(text, self.commands, self.cfg["match_threshold"])
        now = f"{datetime.datetime.now():%H:%M}"
        if cmd:
            self.log(f"Eşleşti: {cmd['name']} (%{int(score)})")
            self.state["last"] = {"text": text, "cmd": cmd["name"], "time": now}
            threading.Thread(target=run_command, args=(cmd, self.log), daemon=True).start()
        else:
            self.log(f"Eşleşen komut yok (en iyi skor %{int(score)}).")
            self.state["last"] = {"text": text, "cmd": None, "time": now}


# ----------------------------------------------------------------------------
# Ses motoru
# ----------------------------------------------------------------------------
class Engine(threading.Thread):
    def __init__(self, core):
        super().__init__(daemon=True)
        self.core = core
        self.stop = threading.Event()
        self.reopen = threading.Event()
        self.models = {}
        self.whisper = None

    def run(self):
        while not self.stop.is_set():
            try:
                self.session()
            except Exception as e:
                self.core.log(f"Motor hatası: {e!r} - 3 sn sonra yeniden denenecek.")
                self.core.set_state("error", "Hata - yeniden deneniyor")
                self.stop.wait(3)

    def get_model(self, name):
        from vosk import Model
        if name not in self.models:
            d = MODELS / ("vosk-tr" if name == "tr" else "vosk-en")
            if not d.exists():
                raise FileNotFoundError(f"Model klasörü yok: {d}")
            self.core.set_state("loading", "Model yükleniyor...")
            self.models[name] = Model(str(d))
        return self.models[name]

    def get_whisper(self):
        if self.whisper is None:
            from faster_whisper import WhisperModel
            d = MODELS / "whisper"
            if not d.exists():
                raise FileNotFoundError(f"Model klasörü yok: {d}")
            self.core.set_state("loading", "Whisper yükleniyor...")
            self.whisper = WhisperModel(str(d), device="cpu", compute_type="int8")
            self.core.log("Whisper yüklendi.")
        return self.whisper

    def session(self):
        import sounddevice as sd
        from vosk import KaldiRecognizer, SetLogLevel
        SetLogLevel(-1)
        core, cfg = self.core, self.core.cfg
        self.reopen.clear()

        wake = cfg["wake_lang"] if cfg["wake_lang"] in WAKE_GRAMMAR else "tr"
        need = {wake} | ({"tr"} if cfg["cmd_engine"] == "light" else set())
        for k in list(self.models):
            if k not in need:
                del self.models[k]
        if cfg["cmd_engine"] == "light" and self.whisper is not None:
            self.whisper = None
        gc.collect()
        for n in need:
            self.get_model(n)
        wake_model = self.models[wake]
        rec = KaldiRecognizer(wake_model, SR, json.dumps(WAKE_GRAMMAR[wake] + ["[unk]"]))

        aq = queue.Queue()

        def cb(indata, frames, t, status):
            aq.put(bytes(indata))

        noise = deque(maxlen=80)
        with sd.RawInputStream(samplerate=SR, blocksize=2000, device=cfg.get("mic_device"),
                               dtype="int16", channels=1, callback=cb):
            core.log("Mikrofon açıldı.")
            last, last_data = None, time.time()
            while not self.stop.is_set() and not self.reopen.is_set():
                core.state["beat"] = time.time()
                try:
                    data = aq.get(timeout=0.5)
                except queue.Empty:
                    if time.time() - last_data > 4:
                        raise RuntimeError("Mikrofondan ses gelmiyor")
                    continue
                last_data = time.time()
                arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(arr ** 2)))
                core.state["level"] = min(1.0, rms / 3000.0)

                if not cfg.get("listening", True):
                    if last != "pause":
                        core.set_state("pause", "Dinleme kapalı")
                        rec.Reset()
                        last = "pause"
                    continue
                if last != "wait":
                    core.set_state("wait", "“Hey Tank” bekleniyor")
                    last = "wait"

                noise.append(rms)
                if rec.AcceptWaveform(data):
                    txt = json.loads(rec.Result()).get("text", "")
                else:
                    txt = json.loads(rec.PartialResult()).get("partial", "")
                if txt and txt != "[unk]":
                    core.state["heard"] = txt
                if is_wake(txt):
                    rec.Reset()
                    thr = float(min(2500.0, max(300.0, np.median(noise) * 2.5)))
                    try:
                        self.handle_command(aq, thr)
                    except Exception as e:
                        core.log(f"Komut hatası: {e!r}")
                    self.flush(aq)
                    rec.Reset()
                    last = None

    @staticmethod
    def flush(aq):
        while not aq.empty():
            try:
                aq.get_nowait()
            except queue.Empty:
                break

    def handle_command(self, aq, thr):
        core, cfg = self.core, self.core.cfg
        core.set_state("listen", "Dinliyorum...")
        core.log("“Hey Tank” algılandı.")
        if cfg.get("beep", True):
            try:
                import winsound
                winsound.Beep(900, 120)
            except Exception:
                pass
        self.flush(aq)
        audio = self.record(aq, thr, cfg.get("max_record_sec", 7))
        if audio is None:
            core.log("Konuşma algılanmadı.")
            return
        core.set_state("think", "Anlaşılıyor...")
        cmds = [c for c in core.commands if c.get("enabled", True)]
        if cfg["cmd_engine"] == "light":
            text = self.transcribe_light(audio, cmds)
        else:
            text = self.transcribe_whisper(audio, cmds)
        core.log(f"Duyulan: “{text}”")
        core.handle_text(text)

    def record(self, aq, thr, max_sec):
        chunks, started, silence = [], False, 0.0
        t0 = time.time()
        while time.time() - t0 < max_sec and not self.stop.is_set():
            self.core.state["beat"] = time.time()
            try:
                d = aq.get(timeout=0.5)
            except queue.Empty:
                continue
            a = np.frombuffer(d, dtype=np.int16)
            rms = float(np.sqrt(np.mean(a.astype(np.float32) ** 2)))
            self.core.state["level"] = min(1.0, rms / 3000.0)
            chunks.append(a)
            if rms > thr:
                started, silence = True, 0.0
            elif started:
                silence += len(a) / SR
            if started and silence >= 0.9:
                break
            if not started and time.time() - t0 > 4:
                return None
        return np.concatenate(chunks) if (started and chunks) else None

    def transcribe_whisper(self, audio, cmds):
        w = self.get_whisper()
        self.core.set_state("think", "Anlaşılıyor...")
        prompt = ", ".join(p for c in cmds for p in c.get("phrases", []))[:350]
        segs, _ = w.transcribe(audio.astype(np.float32) / 32768.0, language="tr",
                               beam_size=1, initial_prompt=prompt or None,
                               condition_on_previous_text=False)
        return " ".join(s.text for s in segs).strip()

    def transcribe_light(self, audio, cmds):
        from vosk import KaldiRecognizer
        phrases = sorted({vosk_phrase(p) for c in cmds for p in c.get("phrases", []) if vosk_phrase(p)})
        rec = KaldiRecognizer(self.models["tr"], SR, json.dumps(phrases + ["[unk]"], ensure_ascii=False))
        rec.AcceptWaveform(audio.tobytes())
        return json.loads(rec.FinalResult()).get("text", "").replace("[unk]", "").strip()


# ----------------------------------------------------------------------------
# JS <-> Python koprusu (alt cizgili ozellikler pywebview'a gorunmez)
# ----------------------------------------------------------------------------
class Api:
    def __init__(self, core, app):
        self._core = core
        self._app = app

    def init(self):
        mics = [{"id": None, "name": "Varsayılan mikrofon"}]
        try:
            import sounddevice as sd
            ha = sd.default.hostapi
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and d["hostapi"] == ha:
                    mics.append({"id": i, "name": d["name"]})
        except Exception:
            pass
        return {"commands": self._core.commands, "config": self._core.cfg, "mics": mics,
                "system_actions": SYSTEM_ACTIONS, "autostart": get_autostart(), "version": VERSION}

    def poll(self, since):
        c = self._core
        with c.lock:
            logs = [l for l in c.logs if l[0] > since]
        s = dict(c.state)
        s["beat_age"] = time.time() - s.pop("beat", time.time())
        s["logs"] = [l[1] for l in logs]
        s["seq"] = logs[-1][0] if logs else since
        return s

    def save_command(self, cmd):
        c = self._core
        cmd["phrases"] = [p.strip() for p in cmd.get("phrases", []) if p.strip()]
        cmd["targets"] = [t.strip() for t in cmd.get("targets", []) if t.strip()]
        if not cmd.get("id"):
            cmd["id"] = uuid.uuid4().hex[:8]
            cmd["enabled"] = True
            c.commands.append(cmd)
        else:
            for i, x in enumerate(c.commands):
                if x["id"] == cmd["id"]:
                    c.commands[i] = cmd
        c.save_commands()
        return c.commands

    def delete_command(self, cid):
        c = self._core
        c.commands[:] = [x for x in c.commands if x["id"] != cid]
        c.save_commands()
        return c.commands

    def toggle_command(self, cid, enabled):
        for x in self._core.commands:
            if x["id"] == cid:
                x["enabled"] = bool(enabled)
        self._core.save_commands()
        return True

    def test_command(self, cid):
        for x in self._core.commands:
            if x["id"] == cid:
                self._core.log(f"Deneme: {x['name']}")
                threading.Thread(target=run_command, args=(x, self._core.log), daemon=True).start()
        return True

    def try_text(self, text):
        self._core.log(f"Yazılan: “{text}”")
        self._core.handle_text(text)
        return True

    def set_config(self, key, value):
        c = self._core
        if key not in DEFAULT_CONFIG:
            return False
        if key == "mic_device":
            value = None if value in (None, "") else int(value)
        elif key in ("match_threshold", "max_record_sec"):
            value = int(value)
        elif key in ("beep", "listening"):
            value = bool(value)
        c.cfg[key] = value
        c.save_cfg()
        if key in ("mic_device", "wake_lang", "cmd_engine"):
            c.engine.reopen.set()
        return True

    def set_autostart(self, on):
        try:
            set_autostart(bool(on))
        except Exception as e:
            self._core.log(f"Başlangıç ayarı yazılamadı: {e!r}")
        return get_autostart()

    def pick_file(self):
        import webview
        try:
            kind = webview.FileDialog.OPEN
        except AttributeError:
            kind = webview.OPEN_DIALOG
        r = self._app.window.create_file_dialog(kind)
        return r[0] if r else None

    def hide(self):
        self._app.hide()
        return True


# ----------------------------------------------------------------------------
# Uygulama / pencere / tepsi
# ----------------------------------------------------------------------------
def message_box(text, title="Hey Tank"):
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        pass


class App:
    def __init__(self, hidden):
        self.core = Core()
        self.hidden = hidden
        self.window = None
        self.tray = None

    def hide(self):
        if self.window:
            self.window.hide()

    def show(self):
        if self.window:
            self.window.show()

    def quit(self):
        self.core.engine.stop.set()
        try:
            if self.tray:
                self.tray.stop()
        except Exception:
            pass
        os._exit(0)

    def on_closing(self):
        if self.tray:
            self.window.hide()
            return False
        return True

    def setup_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (64, 64), "#0E0E0E")
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((24, 10, 40, 38), radius=8, outline="#EDEDED", width=4)
            d.arc((14, 22, 50, 50), 0, 180, fill="#EDEDED", width=4)
            d.line((32, 50, 32, 58), fill="#EDEDED", width=4)
            menu = pystray.Menu(pystray.MenuItem("Göster", lambda: self.show(), default=True),
                                pystray.MenuItem("Çıkış", lambda: self.quit()))
            self.tray = pystray.Icon("HeyTank", img, "Hey Tank", menu)
            self.tray.run_detached()
        except Exception as e:
            self.core.log(f"Tepsi simgesi açılamadı: {e!r}")
            self.tray = None

    def run(self):
        import webview
        if get_autostart():
            try:
                set_autostart(True)   # klasor tasinmissa yolu guncelle
            except Exception:
                pass
        self.core.engine.start()
        self.setup_tray()
        self.window = webview.create_window(
            "Hey Tank", url=str(UI_FILE), js_api=Api(self.core, self),
            width=1100, height=680, min_size=(900, 560),
            background_color="#0E0E0E", hidden=self.hidden)
        self.window.events.closing += self.on_closing
        try:
            webview.start(gui="edgechromium", storage_path=str(BASE / "webview-data"))
        except Exception as e:
            message_box("Arayüz açılamadı. Microsoft Edge WebView2 Runtime gerekli "
                        "(Windows 11'de hazır gelir).\n\n" + repr(e))
        self.quit()


def single_instance():
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW(None, False, "HeyTank_single_instance")
        return ctypes.get_last_error() != 183
    except Exception:
        return True


if __name__ == "__main__":
    if single_instance():
        App(hidden="--tray" in sys.argv).run()
