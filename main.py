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
import secrets
import subprocess
import http.server
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
WHISPER_DIRS = {"turbo": MODELS / "whisper-turbo", "small": MODELS / "whisper-small"}

DEFAULT_CONFIG = {
    "match_threshold": 80,
    "max_record_sec": 7,
    "beep": True,                # "Hey Tank" sonrasi bip: varsayilan ACIK
    "mic_device": None,
    "listening": True,
    "wake_lang": "tr",          # tr | en
    "cmd_engine": "turbo",      # turbo (dogru) | small (hizli) | light (hafif)
    "keep_warm": 30,            # Whisper bellekte kalma suresi (sn): 0 hemen birak, -1 hep acik
    "strict_wake": True,        # True: "hey" + "tank" birlikte gerekir
}

WAKE_GRAMMAR = {
    "tr": ["hey tank", "ey tank", "hey tenk", "hey tanık"],
    "en": ["hey tank", "hay tank", "hey thank", "hey tang"],
}
HEY_TOKENS = {"hey", "ey", "hay", "he", "hei"}
TANK_TOKENS = {"tank", "tenk", "tanık", "thank", "tang"}


def is_wake(text: str, strict: bool = True) -> bool:
    t = text.split()
    if not strict:
        return any(w in TANK_TOKENS for w in t)
    return any(a in HEY_TOKENS and b in TANK_TOKENS for a, b in zip(t, t[1:]))


def build_wake(model, wake, strict):
    """Modelin sozlugunde olmayan kelimeleri ayiklar. 'hey' bilinmiyorsa tek 'tank'a duser."""
    def known(w):
        try:
            return model.vosk_model_find_word(w) >= 0
        except Exception:
            return True
    phrases = [p for p in WAKE_GRAMMAR[wake] if all(known(w) for w in p.split())]
    if strict and phrases:
        return phrases, True
    singles = [w for w in ("tank", "tenk", "tanık", "thank") if known(w)] or ["tank"]
    return singles, False


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


def _beep():
    try:
        import winsound
        winsound.Beep(900, 110)
    except Exception:
        pass


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
    tw = t.split()
    best_cmd, best = None, 0
    for cmd in commands:
        if not cmd.get("enabled", True):
            continue
        for ph in cmd.get("phrases", []):
            p = norm(ph)
            if not p:
                continue
            k = len(p.split())
            score = fuzz.ratio(t, p)
            # soylenen cumle komuttan uzunsa ("youtube'u acar misin") kelime pencereleriyle karsilastir
            for i in range(0, max(1, len(tw) - k + 1)):
                w = " ".join(tw[i:i + k])
                score = max(score, fuzz.ratio(w, p))
            if f" {p} " in f" {t} ":
                score = max(score, 92)
            if score > best:
                best_cmd, best = cmd, score
    return (best_cmd, best) if best >= threshold else (None, best)


_HEY = {norm(x) for x in HEY_TOKENS} | {"heytank", "eytank", "haytank", "heytenk"}
_TANK = {norm(x) for x in TANK_TOKENS}


def strip_wake(text: str) -> str:
    """Cümlenin basindaki 'hey tank' benzeri kelimeleri (Whisper'in yazdigi haliyle) atar."""
    words = text.split()
    n = 0
    while words and n < 3:
        w = norm(words[0])
        if w in _HEY or w in _TANK or (len(w) >= 3 and fuzz.ratio(w, "tank") >= 80):
            words.pop(0)
            n += 1
        else:
            break
    return " ".join(words).strip()


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
        if self.cfg.get("cmd_engine") not in ("turbo", "small", "light"):
            self.cfg["cmd_engine"] = "turbo"
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
        self.whisper_key = None
        self.wlock = threading.Lock()
        self.busy = False
        self.last_use = time.time()

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

    def get_whisper(self, quiet=False):
        key = self.core.cfg.get("cmd_engine", "turbo")
        if key not in WHISPER_DIRS:
            key = "turbo"
        with self.wlock:
            if self.whisper is not None and self.whisper_key == key:
                return self.whisper
            self.whisper = None
            gc.collect()
            from faster_whisper import WhisperModel
            d = WHISPER_DIRS[key]
            if not d.exists():
                raise FileNotFoundError(f"Model klasörü yok: {d}")
            if not quiet:
                self.core.set_state("loading", "Model yükleniyor...")
            t0 = time.time()
            self.whisper = WhisperModel(str(d), device="cpu", compute_type="int8",
                                        cpu_threads=max(2, min(6, os.cpu_count() or 4)))
            self.whisper_key = key
            self.core.log(f"Whisper ({key}) yüklendi ({time.time() - t0:.1f} sn).")
            return self.whisper

    def unload_whisper(self):
        with self.wlock:
            if self.whisper is not None:
                self.whisper = None
                self.whisper_key = None
                gc.collect()
                self.core.log("Whisper bellekten bırakıldı.")

    def _preload(self):
        try:
            self.get_whisper(quiet=True)   # kullanici konusurken arka planda yuklenir
        except Exception as e:
            self.core.log(f"Model yüklenemedi: {e!r}")

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
        if self.whisper is not None and self.whisper_key != cfg["cmd_engine"]:
            self.unload_whisper()
        gc.collect()
        for n in need:
            self.get_model(n)
        wake_model = self.models[wake]
        phrases, strict = build_wake(wake_model, wake, cfg.get("strict_wake", True))
        if strict:
            core.log("Uyandırma: “hey tank” (hey + tank birlikte).")
        elif cfg.get("strict_wake", True):
            core.log("Uyarı: model “hey” kelimesini bilmiyor, yalnızca “tank” ile uyanıyor. "
                     "Ayarlardan İngilizce modeli dene.")
        else:
            core.log("Uyandırma: yalnızca “tank” (sıkı algılama kapalı).")
        rec = KaldiRecognizer(wake_model, SR, json.dumps(phrases + ["[unk]"], ensure_ascii=False))

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
                keep = cfg.get("keep_warm", 30)
                if (self.whisper is not None and keep >= 0 and not self.busy
                        and time.time() - self.last_use > keep):
                    self.unload_whisper()
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
                if is_wake(txt, strict):
                    rec.Reset()
                    thr = float(min(2500.0, max(300.0, np.median(noise) * 2.5)))
                    try:
                        self.handle_command(aq, thr)
                    except Exception as e:
                        core.log(f"Komut hatası: {e!r}")
                    self.flush(aq)
                    rec.Reset()
                    last, last_data = None, time.time()

    @staticmethod
    def flush(aq):
        while not aq.empty():
            try:
                aq.get_nowait()
            except queue.Empty:
                break

    def handle_command(self, aq, thr):
        core, cfg = self.core, self.core.cfg
        self.busy = True
        try:
            core.set_state("listen", "Dinliyorum...")
            core.log("“Hey Tank” algılandı.")
            if cfg["cmd_engine"] != "light":
                threading.Thread(target=self._preload, daemon=True).start()
            if cfg.get("beep", True):
                threading.Thread(target=_beep, daemon=True).start()   # kaydi bloklamasin
            text = ""
            for attempt in range(2):
                audio = self.record(aq, thr, cfg.get("max_record_sec", 7))
                if audio is None:
                    core.log("Konuşma algılanmadı.")
                    return
                core.set_state("think", "Anlaşılıyor...")
                cmds = [c for c in core.commands if c.get("enabled", True)]
                if cfg["cmd_engine"] == "light":
                    raw = self.transcribe_light(audio, cmds)
                else:
                    raw = self.transcribe_whisper(audio, cmds)
                text = strip_wake(raw)
                core.log(f"Duyulan: “{raw}”")
                if text:
                    break
                if attempt == 0:   # sadece "hey tank" duyuldu -> asil komutu bekle
                    core.set_state("listen", "Dinliyorum...")
            if text:
                core.handle_text(text)
        finally:
            self.busy = False
            self.last_use = time.time()

    def record(self, aq, thr, max_sec):
        """Uyandirmadan sonra konusmanin BASLAMASINI bekler, sonra susana kadar kaydeder.
        Kuyruk bosaltilmaz: 'Hey Tank, youtube ac' tek nefeste soylense bile komut kaybolmaz."""
        pre = deque(maxlen=4)           # konusma baslamadan onceki ~0.5 sn
        chunks, started, hot, silence, n = [], False, 0, 0.0, 0
        t0, t_on = time.time(), None
        while not self.stop.is_set():
            now = time.time()
            self.core.state["beat"] = now
            if started and now - t_on > max_sec:
                break
            if not started and now - t0 > 5:
                return None
            try:
                d = aq.get(timeout=0.5)
            except queue.Empty:
                continue
            a = np.frombuffer(d, dtype=np.int16)
            rms = float(np.sqrt(np.mean(a.astype(np.float32) ** 2)))
            self.core.state["level"] = min(1.0, rms / 3000.0)
            n += 1
            if not started:
                pre.append(a)
                hot = hot + 1 if (n > 3 and rms > thr) else 0   # ilk ~0.4 sn: bip/"tank" kuyrugu
                if hot >= 2:
                    started, t_on, silence = True, now, 0.0
                    chunks = list(pre)
            else:
                chunks.append(a)
                silence = 0.0 if rms > thr else silence + len(a) / SR
                if silence >= 1.0:
                    break
        return np.concatenate(chunks) if (started and chunks) else None

    def transcribe_whisper(self, audio, cmds):
        w = self.get_whisper()
        self.core.set_state("think", "Anlaşılıyor...")
        x = audio.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(x ** 2))) + 1e-6
        x = np.clip(x * float(np.clip(0.06 / rms, 1.0, 6.0)), -1.0, 1.0)   # sessiz mikrofonu yukselt
        pad = np.zeros(int(0.3 * SR), dtype=np.float32)
        x = np.concatenate([pad, x, pad])
        words = []
        for c in cmds:
            for p in c.get("phrases", []):
                for wd in p.replace("'", " ").split():
                    if len(wd) >= 3 and wd not in words:
                        words.append(wd)
        hot = " ".join(words)[:300]
        kw = dict(language="tr", beam_size=5, temperature=0.0, without_timestamps=True,
                  condition_on_previous_text=False)
        try:
            segs, _ = w.transcribe(x, hotwords=hot or None, **kw)
        except TypeError:   # eski faster-whisper
            segs, _ = w.transcribe(x, **kw)
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
        age = time.time() - s.pop("beat", time.time())
        busy = s["state"] in ("loading", "think", "listen")   # model yukleme/anlama uzun surebilir
        s["stale"] = age > (180 if busy else 15)
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
        elif key in ("match_threshold", "max_record_sec", "keep_warm"):
            value = int(value)
        elif key in ("beep", "listening", "strict_wake"):
            value = bool(value)
        c.cfg[key] = value
        c.save_cfg()
        if key in ("mic_device", "wake_lang", "cmd_engine", "strict_wake"):
            c.engine.reopen.set()
        return True

    def add_phrase(self, cid, text):
        c = self._core
        text = (text or "").strip()
        for x in c.commands:
            if x["id"] == cid and text and text not in x["phrases"]:
                x["phrases"].append(text)
                c.log(f"Cümle eklendi: “{text}” → {x['name']}")
        c.save_commands()
        return c.commands

    def set_autostart(self, on):
        try:
            set_autostart(bool(on))
        except Exception as e:
            self._core.log(f"Başlangıç ayarı yazılamadı: {e!r}")
        return get_autostart()

    def pick_file(self):
        w = self._app.window
        if w is not None:
            import webview
            try:
                kind = webview.FileDialog.OPEN
            except AttributeError:
                kind = webview.OPEN_DIALOG
            r = w.create_file_dialog(kind)
            return r[0] if r else None
        import tkinter                      # tarayici modunda
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            p = filedialog.askopenfilename(title="Program / dosya seç")
        finally:
            root.destroy()
        return p or None

    def quit_app(self):
        self._app.quit()
        return True

    def hide(self):
        self._app.hide()
        return True


# ----------------------------------------------------------------------------
# Internetten inen zip'teki "Zone.Identifier" isaretini kaldir.
# .NET bu isaretli DLL'leri yuklemeyi reddeder -> "Failed to resolve Python.Runtime.Loader.Initialize"
# ----------------------------------------------------------------------------
def unblock_files():
    try:
        for root in {BASE, RES}:
            for dp, dn, fn in os.walk(root):
                dn[:] = [d for d in dn if d not in ("models", "webview-data")]
                for f in fn:
                    if f.lower().endswith((".dll", ".pyd", ".exe", ".config")):
                        try:
                            os.remove(os.path.join(dp, f) + ":Zone.Identifier")
                        except OSError:
                            pass
    except Exception:
        pass


def selftest():
    """CI'da: derlenen exe pythonnet/pywebview ve ses kutuphanelerini yukleyebiliyor mu?"""
    out = BASE / "selftest.txt"
    code = 0
    try:
        unblock_files()
        import clr  # noqa: F401  (pythonnet)
        import webview  # noqa: F401
        import faster_whisper  # noqa: F401
        import vosk  # noqa: F401
        import sounddevice  # noqa: F401
        out.write_text("OK", encoding="utf-8")
    except Exception as e:
        out.write_text(f"HATA: {e!r}", encoding="utf-8")
        code = 1
    os._exit(code)


# ----------------------------------------------------------------------------
# Yedek arayuz: pywebview/pythonnet calismazsa Edge/Chrome "uygulama modu" + yerel sunucu
# ----------------------------------------------------------------------------
SHIM = r"""<script>(function(){if(window.pywebview)return;var b=location.pathname.replace(/[^\/]*$/,'');
window.pywebview={api:new Proxy({},{get:function(_,n){return function(){var a=Array.prototype.slice.call(arguments);
return fetch(b+'api/'+n,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(a)}).then(function(r){return r.json()})}}})};
window.addEventListener('load',function(){window.dispatchEvent(new Event('pywebviewready'))})})();</script>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _rest(self):
        if not self.headers.get("Host", "").startswith("127.0.0.1"):
            return None
        parts = self.path.split("?")[0].split("/", 2)
        if len(parts) < 3 or parts[1] != self.server.token:
            return None
        return parts[2]

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        rest = self._rest()
        icon = RES / "assets" / "icon.png"
        if rest in ("", "index.html"):
            html = UI_FILE.read_text(encoding="utf-8").replace(
                "</head>", '<link rel="icon" href="icon.png">' + SHIM + "</head>", 1)
            self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))
        elif rest == "icon.png" and icon.exists():
            self._send(200, "image/png", icon.read_bytes())
        else:
            self._send(404, "text/plain", b"yok")

    def do_POST(self):
        rest = self._rest()
        if not rest or not rest.startswith("api/"):
            return self._send(404, "text/plain", b"yok")
        name = rest[4:]
        fn = getattr(self.server.api, name, None)
        if name.startswith("_") or not callable(fn):
            return self._send(404, "text/plain", b"yok")
        try:
            n = int(self.headers.get("Content-Length", "0"))
            args = json.loads(self.rfile.read(n) or b"[]")
            body = json.dumps(fn(*args), ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
        except Exception as e:
            self._send(500, "application/json", json.dumps({"error": repr(e)}).encode("utf-8"))


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
        self.httpd = None
        self.proc = None

    def set_window_icon(self, *a):
        """Gorev cubugu / baslik cubugu ikonu (exe ikonu gomulu olmasa bile T gorunur)."""
        try:
            ico = RES / "assets" / "icon.ico"
            if not ico.exists():
                return
            u = ctypes.windll.user32
            u.FindWindowW.restype = ctypes.c_void_p
            u.LoadImageW.restype = ctypes.c_void_p
            u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
            hwnd = u.FindWindowW(None, "Hey Tank")
            if not hwnd:
                return
            big = u.LoadImageW(None, str(ico), 1, 32, 32, 0x10)
            small = u.LoadImageW(None, str(ico), 1, 16, 16, 0x10)
            if big:
                u.SendMessageW(hwnd, 0x80, 1, big)
            if small:
                u.SendMessageW(hwnd, 0x80, 0, small)
        except Exception:
            pass

    def hide(self):
        if self.window:
            self.window.hide()

    def show(self):
        if self.window:
            self.window.show()
        elif self.httpd:
            self.open_browser_window()

    def start_browser_ui(self, show=True):
        if self.httpd is None:
            self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            self.httpd.token = secrets.token_urlsafe(12)
            self.httpd.api = Api(self.core, self)
            threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        if show:
            self.open_browser_window()

    def open_browser_window(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        url = f"http://127.0.0.1:{self.httpd.server_port}/{self.httpd.token}/"
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                     os.environ.get("LOCALAPPDATA")):
            if not base:
                continue
            for exe in (Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                        Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"):
                if exe.exists():
                    try:
                        self.proc = subprocess.Popen(
                            [str(exe), f"--app={url}",
                             f"--user-data-dir={BASE / 'webview-data' / 'browser'}",
                             "--window-size=1100,720", "--no-first-run",
                             "--no-default-browser-check"])
                        return
                    except Exception as e:
                        self.core.log(f"Tarayıcı açılamadı: {e!r}")
        import webbrowser
        webbrowser.open(url)

    def watch_show(self):
        """Uygulama zaten calisirken exe'ye tekrar cift tiklanirsa pencereyi one getir."""
        f = BASE / ".show"
        while True:
            time.sleep(1)
            try:
                if f.exists():
                    f.unlink()
                    self.show()
            except Exception:
                pass

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
            from icon_draw import draw
            img = draw(64)
            menu = pystray.Menu(pystray.MenuItem("Göster", lambda: self.show(), default=True),
                                pystray.MenuItem("Çıkış", lambda: self.quit()))
            self.tray = pystray.Icon("HeyTank", img, "Hey Tank", menu)
            self.tray.run_detached()
        except Exception as e:
            self.core.log(f"Tepsi simgesi açılamadı: {e!r}")
            self.tray = None

    def run(self):
        unblock_files()   # pythonnet yuklenmeden once
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HeyTank.App")
        except Exception:
            pass
        if get_autostart():
            try:
                set_autostart(True)   # klasor tasinmissa yolu guncelle
            except Exception:
                pass
        self.core.engine.start()
        self.setup_tray()
        threading.Thread(target=self.watch_show, daemon=True).start()

        ok = False
        if "--browser" not in sys.argv:
            try:
                import webview
                self.window = webview.create_window(
                    "Hey Tank", url=str(UI_FILE), js_api=Api(self.core, self),
                    width=1100, height=680, min_size=(900, 560),
                    background_color="#0E0E0E", hidden=self.hidden)
                self.window.events.closing += self.on_closing
                self.window.events.loaded += self.set_window_icon
                self.window.events.shown += self.set_window_icon
                webview.start(gui="edgechromium", storage_path=str(BASE / "webview-data"))
                ok = True
            except Exception as e:
                self.window = None
                self.core.log(f"Pencere modu açılamadı ({e!r}). Tarayıcı moduna geçildi.")
        if ok:
            self.quit()

        # yedek: Edge/Chrome uygulama penceresi
        self.start_browser_ui(show=not self.hidden)
        while True:
            time.sleep(1)
            if self.tray is None and self.proc is not None and self.proc.poll() is not None:
                self.quit()


def single_instance():
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW(None, False, "HeyTank_single_instance")
        return ctypes.get_last_error() != 183
    except Exception:
        return True


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif single_instance():
        App(hidden="--tray" in sys.argv).run()
    else:
        try:
            (BASE / ".show").write_text("1")   # calisan kopya pencereyi one getirir
        except Exception:
            pass
