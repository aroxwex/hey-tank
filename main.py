# Hey Tank - Windows icin portable, cevrimdisi sesli komut asistani
import sys
import os

# --noconsole ile derlenince stdout/stderr None olur; bazi kutuphaneler cokmesin
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import json
import re
import time
import queue
import threading
import random
import ctypes
import datetime
from collections import deque
from pathlib import Path

import numpy as np
import customtkinter as ctk
from tkinter import filedialog, messagebox
from rapidfuzz import fuzz

# ----------------------------------------------------------------------------
# Yollar (hepsi exe'nin bulundugu klasorde -> portable)
# ----------------------------------------------------------------------------
def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE = base_dir()
CONFIG_PATH = BASE / "config.json"
COMMANDS_PATH = BASE / "commands.json"
MODELS = BASE / "models"
SR = 16000

DEFAULT_CONFIG = {
    "match_threshold": 80,
    "max_record_sec": 7,
    "beep": True,
    "mic_device": None,
    "listening": True,
}

WAKE_PHRASES = ["hey tank", "hay tank", "hey thank", "hey tang"]
WAKE_GRAMMAR = json.dumps(WAKE_PHRASES + ["[unk]"])

# ----------------------------------------------------------------------------
# Sistem eylemleri
# ----------------------------------------------------------------------------
SYSTEM_ACTIONS = {
    "Bilgisayarı kilitle": "lock",
    "Sesi aç": "vol_up",
    "Sesi kıs": "vol_down",
    "Sesi kapat / aç (sessiz)": "mute",
    "Müziği durdur / devam ettir": "play_pause",
    "Sonraki şarkı": "next",
    "Önceki şarkı": "prev",
}
SYSTEM_NAMES = {v: k for k, v in SYSTEM_ACTIONS.items()}
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


def open_target(target, args=""):
    target = target.strip().strip('"')
    if args:
        os.startfile(target, arguments=args)
    else:
        os.startfile(target)


def run_command(cmd, log):
    if cmd.get("system"):
        run_system(cmd["system"])
        return
    targets = cmd.get("targets") or []
    if not targets:
        log("Bu komutun hedefi yok.")
        return
    chosen = [random.choice(targets)] if cmd.get("mode") == "random" else targets
    for t in chosen:
        try:
            open_target(t, cmd.get("args", ""))
            log(f"Açıldı: {t}")
        except Exception as e:
            log(f"Açılamadı ({t}): {e}")


# ----------------------------------------------------------------------------
# Varsayilan komutlar, kaydet/yukle
# ----------------------------------------------------------------------------
def _c(name, phrases, targets=None, system=None, mode="all", args="", enabled=True):
    return {"name": name, "phrases": phrases, "targets": targets or [],
            "system": system, "mode": mode, "args": args, "enabled": enabled}


def default_commands():
    return [
        _c("YouTube", ["youtube aç", "youtube'u aç"], ["https://www.youtube.com"]),
        _c("Google", ["google aç", "google'ı aç"], ["https://www.google.com"]),
        _c("Spotify (web)", ["spotify aç", "spotify'ı aç"], ["https://open.spotify.com"]),
        _c("Not Defteri", ["not defteri aç", "not defterini aç"], ["notepad.exe"]),
        _c("Hesap Makinesi", ["hesap makinesi aç", "hesap makinesini aç"], ["calc.exe"]),
        _c("Dosya Gezgini", ["dosya gezgini aç", "dosya gezginini aç"], ["explorer.exe"]),
        _c("CS2 (Steam)", ["cs2 aç", "cs2'yi aç", "cs aç", "counter strike aç"],
           ["steam://rungameid/730"]),
        _c("Bilgisayarı kilitle", ["bilgisayarı kilitle", "ekranı kilitle"], system="lock"),
        _c("Sesi aç", ["sesi aç", "sesi yükselt"], system="vol_up"),
        _c("Sesi kıs", ["sesi kıs", "sesi azalt"], system="vol_down"),
        _c("Sessiz", ["sesi kapat", "sessiz"], system="mute"),
        _c("Müzik durdur/devam", ["müziği durdur", "müziği devam ettir", "müziği duraklat"],
           system="play_pause"),
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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------------
# Metin esleme (Turkce karakterleri sadelestirip bulanik karsilastirir)
# ----------------------------------------------------------------------------
_TR = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def norm(s: str) -> str:
    s = s.replace("İ", "i").replace("I", "ı").lower().translate(_TR)
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def best_match(text, commands, threshold):
    t = norm(text)
    if not t:
        return None, 0
    best_cmd, best_score = None, 0
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
            if score > best_score:
                best_cmd, best_score = cmd, score
    if best_score >= threshold:
        return best_cmd, best_score
    return None, best_score


# ----------------------------------------------------------------------------
# Ses motoru (ayri thread)
# ----------------------------------------------------------------------------
class Engine(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.stop_flag = threading.Event()
        self.reopen = threading.Event()

    def log(self, m):
        self.app.q.put(("log", m))

    def status(self, s):
        self.app.q.put(("status", s))

    def run(self):
        try:
            self._run()
        except Exception as e:
            self.log(f"HATA: {e!r}")
            self.status("Hata - Kayıt sekmesine bak")

    def _run(self):
        import sounddevice as sd
        from vosk import Model, KaldiRecognizer, SetLogLevel
        from faster_whisper import WhisperModel

        SetLogLevel(-1)
        vosk_dir = MODELS / "vosk-wake"
        whisper_dir = MODELS / "whisper"
        if not vosk_dir.exists() or not whisper_dir.exists():
            raise FileNotFoundError("models klasörü eksik (models/vosk-wake ve models/whisper)")

        self.status("Modeller yükleniyor...")
        wake_model = Model(str(vosk_dir))
        whisper = WhisperModel(str(whisper_dir), device="cpu", compute_type="int8")
        self.log("Modeller yüklendi.")

        noise = deque(maxlen=60)
        while not self.stop_flag.is_set():
            self.reopen.clear()
            aq = queue.Queue()

            def cb(indata, frames, t, status):
                aq.put(bytes(indata))

            rec = KaldiRecognizer(wake_model, SR, WAKE_GRAMMAR)
            with sd.RawInputStream(samplerate=SR, blocksize=2000,
                                   device=self.app.cfg.get("mic_device"),
                                   dtype="int16", channels=1, callback=cb):
                self.log("Mikrofon açıldı.")
                last_state = None
                while not self.stop_flag.is_set() and not self.reopen.is_set():
                    try:
                        data = aq.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if not self.app.cfg.get("listening", True):
                        if last_state != "pause":
                            self.status("Duraklatıldı")
                            rec.Reset()
                            last_state = "pause"
                        continue
                    if last_state != "wait":
                        self.status("“Hey Tank” bekleniyor...")
                        last_state = "wait"

                    arr = np.frombuffer(data, dtype=np.int16)
                    noise.append(float(np.sqrt(np.mean(arr.astype(np.float32) ** 2))))
                    rec.AcceptWaveform(data)
                    partial = json.loads(rec.PartialResult()).get("partial", "").strip()
                    if partial in WAKE_PHRASES:
                        rec.Reset()
                        thr = max(300.0, float(np.median(noise)) * 2.5) if noise else 300.0
                        self.handle_command(aq, whisper, thr)
                        last_state = None

    def handle_command(self, aq, whisper, thr):
        import winsound
        self.status("Dinliyorum...")
        self.log("“Hey Tank” algılandı.")
        if self.app.cfg.get("beep", True):
            try:
                winsound.Beep(900, 120)
            except Exception:
                pass
        while not aq.empty():
            aq.get_nowait()

        audio = self.record(aq, thr, self.app.cfg.get("max_record_sec", 7))
        if audio is None:
            self.log("Konuşma algılanmadı.")
            return
        self.status("Anlaşılıyor...")
        cmds = [c for c in self.app.commands if c.get("enabled", True)]
        prompt = ", ".join(p for c in cmds for p in c.get("phrases", []))[:350]
        segs, _ = whisper.transcribe(
            audio.astype(np.float32) / 32768.0, language="tr", beam_size=1,
            initial_prompt=prompt or None, condition_on_previous_text=False)
        text = " ".join(s.text for s in segs).strip()
        self.log(f"Duyulan: “{text}”")
        self.app.q.put(("match", text))
        while not aq.empty():
            aq.get_nowait()

    def record(self, aq, thr, max_sec):
        chunks, started, silence = [], False, 0.0
        t0 = time.time()
        while time.time() - t0 < max_sec and not self.stop_flag.is_set():
            try:
                d = aq.get(timeout=0.5)
            except queue.Empty:
                continue
            a = np.frombuffer(d, dtype=np.int16)
            rms = float(np.sqrt(np.mean(a.astype(np.float32) ** 2)))
            chunks.append(a)
            if rms > thr:
                started, silence = True, 0.0
            elif started:
                silence += len(a) / SR
            if started and silence >= 0.9:
                break
            if not started and time.time() - t0 > 4:
                return None
        if not started or not chunks:
            return None
        return np.concatenate(chunks)


# ----------------------------------------------------------------------------
# Arayuz
# ----------------------------------------------------------------------------
KIND_OPEN = "Site / program / dosya aç"
KIND_SYS = "Sistem eylemi"
MODE_ALL = "Hepsini aç"
MODE_RANDOM = "Rastgele birini aç"


class EditDialog(ctk.CTkToplevel):
    def __init__(self, parent, cmd, on_save):
        super().__init__(parent)
        self.title("Komut")
        self.geometry("540x680")
        self.cmd = cmd
        self.on_save = on_save
        self.transient(parent)
        pad = dict(padx=16, pady=(10, 2), anchor="w")

        ctk.CTkLabel(self, text="Komut adı").pack(**pad)
        self.name = ctk.CTkEntry(self, width=500)
        self.name.pack(padx=16)

        ctk.CTkLabel(self, text="Söyleyeceğin cümleler (her satıra bir tane)").pack(**pad)
        self.phrases = ctk.CTkTextbox(self, width=500, height=90)
        self.phrases.pack(padx=16)

        ctk.CTkLabel(self, text="Ne yapsın?").pack(**pad)
        self.kind = ctk.CTkOptionMenu(self, values=[KIND_OPEN, KIND_SYS], width=500,
                                      command=lambda _v: self.toggle())
        self.kind.pack(padx=16)

        self.open_frame = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.open_frame, text="Hedefler (her satıra bir tane: site, .exe yolu, steam://... vb.)").pack(**pad)
        self.targets = ctk.CTkTextbox(self.open_frame, width=500, height=110)
        self.targets.pack(padx=16)
        ctk.CTkButton(self.open_frame, text="Dosya seç (.exe vb.)", width=180,
                      command=self.pick_file).pack(padx=16, pady=6, anchor="w")
        ctk.CTkLabel(self.open_frame, text="Birden fazla hedef varsa").pack(**pad)
        self.mode = ctk.CTkOptionMenu(self.open_frame, values=[MODE_ALL, MODE_RANDOM], width=500)
        self.mode.pack(padx=16)
        ctk.CTkLabel(self.open_frame, text="Program parametreleri (isteğe bağlı)").pack(**pad)
        self.args = ctk.CTkEntry(self.open_frame, width=500)
        self.args.pack(padx=16)

        self.sys_frame = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.sys_frame, text="Eylem").pack(**pad)
        self.sysmenu = ctk.CTkOptionMenu(self.sys_frame, values=list(SYSTEM_ACTIONS), width=500)
        self.sysmenu.pack(padx=16)

        self.btns = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkButton(self.btns, text="Kaydet", command=self.save).pack(side="left", padx=8)
        ctk.CTkButton(self.btns, text="İptal", fg_color="gray40", command=self.destroy).pack(side="left", padx=8)

        # mevcut degerleri doldur
        self.name.insert(0, cmd.get("name", ""))
        self.phrases.insert("1.0", "\n".join(cmd.get("phrases", [])))
        if cmd.get("system"):
            self.kind.set(KIND_SYS)
            self.sysmenu.set(SYSTEM_NAMES.get(cmd["system"], list(SYSTEM_ACTIONS)[0]))
        self.targets.insert("1.0", "\n".join(cmd.get("targets", [])))
        self.mode.set(MODE_RANDOM if cmd.get("mode") == "random" else MODE_ALL)
        self.args.insert(0, cmd.get("args", ""))
        self.toggle()
        self.after(150, self.lift)
        self.after(150, self.focus)

    def toggle(self):
        self.open_frame.pack_forget()
        self.sys_frame.pack_forget()
        self.btns.pack_forget()
        (self.sys_frame if self.kind.get() == KIND_SYS else self.open_frame).pack(fill="x")
        self.btns.pack(pady=14)

    def pick_file(self):
        p = filedialog.askopenfilename(parent=self, title="Program / dosya seç")
        if p:
            cur = self.targets.get("1.0", "end").strip()
            self.targets.insert("end", ("\n" if cur else "") + p.replace("/", "\\"))

    def save(self):
        name = self.name.get().strip()
        phrases = [l.strip() for l in self.phrases.get("1.0", "end").splitlines() if l.strip()]
        if not name or not phrases:
            messagebox.showwarning("Eksik bilgi", "Ad ve en az bir cümle gerekli.", parent=self)
            return
        out = dict(self.cmd)
        out.update(name=name, phrases=phrases)
        if self.kind.get() == KIND_SYS:
            out.update(system=SYSTEM_ACTIONS[self.sysmenu.get()], targets=[], args="")
        else:
            targets = [l.strip() for l in self.targets.get("1.0", "end").splitlines() if l.strip()]
            if not targets:
                messagebox.showwarning("Eksik bilgi", "En az bir hedef gerekli.", parent=self)
                return
            out.update(system=None, targets=targets,
                       mode="random" if self.mode.get() == MODE_RANDOM else "all",
                       args=self.args.get().strip())
        self.on_save(out)
        self.destroy()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("Hey Tank")
        self.geometry("820x620")
        self.minsize(700, 500)

        self.cfg = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}
        self.commands = load_json(COMMANDS_PATH, None)
        if not isinstance(self.commands, list):
            self.commands = default_commands()
            save_json(COMMANDS_PATH, self.commands)
        self.q = queue.Queue()
        self.tray = None

        self.build_ui()
        self.refresh_list()
        self.engine = Engine(self)
        self.engine.start()
        self.setup_tray()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.poll)

    # ---- arayuz ----
    def build_ui(self):
        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=12, pady=(12, 6))
        self.status_lbl = ctk.CTkLabel(top, text="Başlatılıyor...", font=ctk.CTkFont(size=16, weight="bold"))
        self.status_lbl.pack(side="left", padx=12, pady=10)
        self.listen_var = ctk.BooleanVar(value=self.cfg["listening"])
        ctk.CTkSwitch(top, text="Dinleme açık", variable=self.listen_var,
                      command=self.on_listen_toggle).pack(side="right", padx=12)

        tabs = ctk.CTkTabview(self)
        tabs.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        t_cmd, t_set, t_log = tabs.add("Komutlar"), tabs.add("Ayarlar"), tabs.add("Kayıt")

        # komutlar
        bar = ctk.CTkFrame(t_cmd, fg_color="transparent")
        bar.pack(fill="x")
        ctk.CTkButton(bar, text="+ Yeni komut", command=self.add_cmd).pack(side="left")
        self.list_frame = ctk.CTkScrollableFrame(t_cmd)
        self.list_frame.pack(fill="both", expand=True, pady=8)

        # ayarlar
        self.mic_map = {"Varsayılan mikrofon": None}
        try:
            import sounddevice as sd
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0:
                    self.mic_map[f"{i}: {d['name']}"] = i
        except Exception:
            pass
        cur = next((k for k, v in self.mic_map.items() if v == self.cfg["mic_device"]), "Varsayılan mikrofon")
        ctk.CTkLabel(t_set, text="Mikrofon").pack(anchor="w", pady=(8, 2))
        self.mic_menu = ctk.CTkOptionMenu(t_set, values=list(self.mic_map), width=560)
        self.mic_menu.set(cur)
        self.mic_menu.pack(anchor="w")
        ctk.CTkButton(t_set, text="Mikrofonu uygula", width=160,
                      command=self.apply_mic).pack(anchor="w", pady=6)

        self.thr_lbl = ctk.CTkLabel(t_set, text="")
        self.thr_lbl.pack(anchor="w", pady=(14, 2))
        self.thr_slider = ctk.CTkSlider(t_set, from_=60, to=100, number_of_steps=40, width=360,
                                        command=self.on_thr)
        self.thr_slider.set(self.cfg["match_threshold"])
        self.thr_slider.pack(anchor="w")
        self.on_thr(self.cfg["match_threshold"])

        self.rec_lbl = ctk.CTkLabel(t_set, text="")
        self.rec_lbl.pack(anchor="w", pady=(14, 2))
        self.rec_slider = ctk.CTkSlider(t_set, from_=3, to=12, number_of_steps=9, width=360,
                                        command=self.on_rec)
        self.rec_slider.set(self.cfg["max_record_sec"])
        self.rec_slider.pack(anchor="w")
        self.on_rec(self.cfg["max_record_sec"])

        self.beep_var = ctk.BooleanVar(value=self.cfg["beep"])
        ctk.CTkSwitch(t_set, text="“Hey Tank” sonrası bip sesi", variable=self.beep_var,
                      command=self.on_beep).pack(anchor="w", pady=16)

        # kayit
        self.log_box = ctk.CTkTextbox(t_log, state="disabled")
        self.log_box.pack(fill="both", expand=True)
        row = ctk.CTkFrame(t_log, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        self.try_entry = ctk.CTkEntry(row, placeholder_text="Komutu yazarak dene (örn: youtube aç)")
        self.try_entry.pack(side="left", fill="x", expand=True)
        self.try_entry.bind("<Return>", lambda e: self.try_text())
        ctk.CTkButton(row, text="Dene", width=80, command=self.try_text).pack(side="left", padx=(8, 0))

    # ---- komut listesi ----
    def refresh_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        for i, cmd in enumerate(self.commands):
            row = ctk.CTkFrame(self.list_frame)
            row.pack(fill="x", pady=3, padx=2)
            var = ctk.BooleanVar(value=cmd.get("enabled", True))
            ctk.CTkSwitch(row, text="", width=46, variable=var,
                          command=lambda i=i, v=var: self.toggle_cmd(i, v)).pack(side="left", padx=8)
            phrases = ", ".join(f"“{p}”" for p in cmd.get("phrases", [])[:3])
            ctk.CTkLabel(row, text=f"{cmd['name']}\n{phrases}", justify="left", anchor="w").pack(
                side="left", fill="x", expand=True, padx=4, pady=6)
            ctk.CTkButton(row, text="Sil", width=50, fg_color="#8b2b2b",
                          command=lambda i=i: self.delete_cmd(i)).pack(side="right", padx=4)
            ctk.CTkButton(row, text="Düzenle", width=70,
                          command=lambda i=i: self.edit_cmd(i)).pack(side="right", padx=4)
            ctk.CTkButton(row, text="▶", width=36, fg_color="gray35",
                          command=lambda i=i: self.test_cmd(i)).pack(side="right", padx=4)

    def save_commands(self):
        save_json(COMMANDS_PATH, self.commands)

    def toggle_cmd(self, i, var):
        self.commands[i]["enabled"] = bool(var.get())
        self.save_commands()

    def add_cmd(self):
        new = _c("Yeni komut", [], [])

        def done(c):
            self.commands.append(c)
            self.save_commands()
            self.refresh_list()
        EditDialog(self, new, done)

    def edit_cmd(self, i):
        def done(c):
            self.commands[i] = c
            self.save_commands()
            self.refresh_list()
        EditDialog(self, self.commands[i], done)

    def delete_cmd(self, i):
        if messagebox.askyesno("Sil", f"“{self.commands[i]['name']}” silinsin mi?"):
            del self.commands[i]
            self.save_commands()
            self.refresh_list()

    def test_cmd(self, i):
        self.add_log(f"Deneme: {self.commands[i]['name']}")
        run_command(self.commands[i], self.add_log)

    def try_text(self):
        text = self.try_entry.get().strip()
        if text:
            self.handle_match(text)

    # ---- ayarlar ----
    def save_cfg(self):
        save_json(CONFIG_PATH, self.cfg)

    def on_listen_toggle(self):
        self.cfg["listening"] = bool(self.listen_var.get())
        self.save_cfg()

    def on_beep(self):
        self.cfg["beep"] = bool(self.beep_var.get())
        self.save_cfg()

    def on_thr(self, v):
        self.cfg["match_threshold"] = int(float(v))
        self.thr_lbl.configure(text=f"Eşleşme hassasiyeti: %{self.cfg['match_threshold']} "
                                    "(düşük = daha toleranslı, yanlış tetikleme riski artar)")
        self.save_cfg()

    def on_rec(self, v):
        self.cfg["max_record_sec"] = int(float(v))
        self.rec_lbl.configure(text=f"Komut için en fazla dinleme süresi: {self.cfg['max_record_sec']} sn")
        self.save_cfg()

    def apply_mic(self):
        self.cfg["mic_device"] = self.mic_map.get(self.mic_menu.get())
        self.save_cfg()
        self.engine.reopen.set()
        self.add_log("Mikrofon yeniden başlatılıyor...")

    # ---- olaylar ----
    def handle_match(self, text):
        cmd, score = best_match(text, self.commands, self.cfg["match_threshold"])
        if cmd:
            self.add_log(f"Eşleşti: {cmd['name']} (%{int(score)})")
            threading.Thread(target=run_command, args=(cmd, self.add_log), daemon=True).start()
        else:
            self.add_log(f"Eşleşen komut yok (en iyi skor %{int(score)}).")

    def add_log(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.q.put(("logline", f"[{ts}] {msg}\n"))

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "status":
                    self.status_lbl.configure(text=val)
                elif kind == "log":
                    self.add_log(val)
                elif kind == "match":
                    self.handle_match(val)
                elif kind == "logline":
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", val)
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self.poll)

    # ---- tepsi / kapatma ----
    def setup_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (64, 64), "#1f6aa5")
            ImageDraw.Draw(img).rectangle((16, 22, 48, 42), fill="white")
            menu = pystray.Menu(
                pystray.MenuItem("Göster", lambda: self.after(0, self.show), default=True),
                pystray.MenuItem("Çıkış", lambda: self.after(0, self.quit_app)))
            self.tray = pystray.Icon("HeyTank", img, "Hey Tank", menu)
            self.tray.run_detached()
        except Exception:
            self.tray = None

    def show(self):
        self.deiconify()
        self.lift()

    def on_close(self):
        if self.tray:
            self.withdraw()
        else:
            self.quit_app()

    def quit_app(self):
        self.engine.stop_flag.set()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.destroy()
        os._exit(0)


if __name__ == "__main__":
    App().mainloop()
