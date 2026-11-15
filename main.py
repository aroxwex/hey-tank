# Hey Tank 2.0 - Gemini Live tabanli, portable Turkce sesli asistan (Windows)
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
import asyncio
import base64
import difflib
import datetime
import threading
import secrets
import subprocess
import http.server
import io
import wave
from collections import deque
from pathlib import Path

import numpy as np

VERSION = "2.0"
SR = 16000          # mikrofon (Gemini girisi)
OUT_SR = 24000      # Gemini ses cikisi


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE = base_dir()
RES = Path(getattr(sys, "_MEIPASS", BASE))
UI_FILE = RES / "ui" / "index.html"
CONFIG_PATH = BASE / "config.json"
SHORTCUTS_PATH = BASE / "shortcuts.json"
OLD_COMMANDS_PATH = BASE / "commands.json"
MODELS = BASE / "models"

LIVE_MODEL_LEGACY = "gemini-3.1-flash-live-preview"   # Google bunu "eski" ilan etti; otomatik secim kullanilir
VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede", "Zephyr", "Orus", "Leda"]

DEFAULT_CONFIG = {
    "cfg_ver": 3,
    "api_key_enc": "",
    "api_version": "",            # bos = SDK varsayilani; baglanti sirasinda calisan surum kaydedilir
    "live_model": "",            # bos = anahtarinin erisebildigi en iyi Live modeli otomatik secilir
    "onboarded": False,          # ilk kurulum ekrani tamamlandi mi
    "wake_mode": "vosk",         # vosk = "Hey Tank" cevrimdisi dinler | off = sadece daire / Ctrl+Alt+T
    "voice": "Charon",
    "speak": True,               # Gemini sesli cevap versin
    "idle_sec": 12,              # konusma olmazsa oturumu kapat (sn)
    "barge_in": False,           # kulaklik modu: Tank konusurken sen de kesebilirsin
    "listening": True,           # "Hey Tank" dinleme
    "wake_lang": "tr",           # tr | en
    "wake_sens": "normal",       # strict | normal | sensitive
    "strict_wake": True,
    "hotkey": True,              # Ctrl+Alt+T
    "mic_device": None,
    "out_device": None,
    "beep_sound": "rising",
    "beep_volume": 60,
    "feedback_sounds": True,
    "tools": {},                 # arac adi -> True/False
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


def build_wake(model, wake, strict, limit=None):
    """Modelin sozlugunde olmayan kelimeleri ayiklar. 'hey' bilinmiyorsa tek 'tank'a duser."""
    def known(w):
        try:
            return model.vosk_model_find_word(w) >= 0
        except Exception:
            return True
    phrases = [p for p in WAKE_GRAMMAR[wake][:limit] if all(known(w) for w in p.split())]
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
    "media_pause": "Müziği durdur (yalnızca çalıyorsa)",
    "media_play": "Müziği başlat / devam ettir",
    "play_pause": "Oynat/Duraklat tuşu (aç-kapa)",
    "next": "Sonraki şarkı",
    "prev": "Önceki şarkı",
    "show_desktop": "Masaüstünü göster",
    "screenshot": "Ekran alıntısı aracı (Win+Shift+S)",
}
VK = {"vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD,
      "play_pause": 0xB3, "next": 0xB0, "prev": 0xB1}
EXT_VK = {0xAF, 0xAE, 0xAD, 0xB3, 0xB0, 0xB1}


def press_combo(vks):
    u = ctypes.windll.user32
    for v in vks:
        u.keybd_event(v, 0, 1 if v in EXT_VK else 0, 0)
    for v in reversed(vks):
        u.keybd_event(v, 0, (1 if v in EXT_VK else 0) | 2, 0)


def press_key(vk, times=1):
    for _ in range(times):
        press_combo([vk])
        time.sleep(0.02)


# Windows medya oturumlari (Spotify, tarayici, ...) ile dogrudan konusur; kucultulmus olsa da calisir.
MEDIA_PS = r"""
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
[void][Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$sessions = @($mgr.GetSessions())
if ($sessions.Count -eq 0) { Write-Output "NOSESSION"; exit 0 }
$cur = $mgr.GetCurrentSession(); if (-not $cur) { $cur = $sessions[0] }
$did = 0
switch ($Action) {
  'pause' { foreach ($s in $sessions) { if ($s.GetPlaybackInfo().PlaybackStatus -eq 'Playing') { [void](Await ($s.TryPauseAsync()) ([bool])); $did++ } } }
  'play'  { [void](Await ($cur.TryPlayAsync()) ([bool])); $did++ }
  'next'  { [void](Await ($cur.TrySkipNextAsync()) ([bool])); $did++ }
  'prev'  { [void](Await ($cur.TrySkipPreviousAsync()) ([bool])); $did++ }
}
Write-Output "DONE $did"
"""


def media_ps(action):
    script = f"$Action = '{action}'\n" + MEDIA_PS
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-EncodedCommand", enc], capture_output=True, text=True, timeout=12,
                           creationflags=0x08000000)
        out = (r.stdout or "").strip()
        return (r.returncode == 0 and ("DONE" in out or "NOSESSION" in out)), (out or (r.stderr or "")[:120])
    except Exception as e:
        return False, repr(e)


def run_system(name, log=None):
    log = log or (lambda m: None)
    if name == "lock":
        ctypes.windll.user32.LockWorkStation()
    elif name in ("vol_up", "vol_down"):
        press_key(VK[name], 5)
    elif name in ("media_pause", "media_play", "next", "prev"):
        act = {"media_pause": "pause", "media_play": "play", "next": "next", "prev": "prev"}[name]
        ok, out = media_ps(act)
        if ok and out.startswith("DONE"):
            n = int(out.split()[-1])
            log("Çalan müzik yok." if (act == "pause" and n == 0) else f"Medya: {act} gönderildi.")
        elif ok:   # NOSESSION
            log("Açık bir medya oturumu bulunamadı.")
            if act != "pause":
                press_key({"play": VK["play_pause"], "next": VK["next"], "prev": VK["prev"]}[act])
        else:
            log(f"Medya denetimi çalışmadı ({out[:80]}); medya tuşu gönderiliyor.")
            press_key({"pause": VK["play_pause"], "play": VK["play_pause"],
                       "next": VK["next"], "prev": VK["prev"]}[act])
    elif name == "show_desktop":
        press_combo([0x5B, 0x44])
    elif name == "screenshot":
        press_combo([0x5B, 0x10, 0x53])
    elif name in VK:
        press_key(VK[name])


# ---- bip sesleri (bellekte uretilir, ses seviyesi ayarli) ----
SND_SR = 22050


def _env(n, fi=0.008, fo=0.04, decay=0.0):
    e = np.ones(n, dtype=np.float32)
    a, b = int(fi * SND_SR), int(fo * SND_SR)
    if a:
        e[:a] = np.linspace(0, 1, a)
    if b:
        e[-b:] *= np.linspace(1, 0, b)
    if decay:
        e *= np.exp(-np.arange(n) / SND_SR * decay)
    return e


def _sine(f, dur, **kw):
    n = int(SND_SR * dur)
    return np.sin(2 * np.pi * f * np.arange(n) / SND_SR) * _env(n, **kw)


def _chirp(f0, f1, dur, **kw):
    n = int(SND_SR * dur)
    ph = 2 * np.pi * np.cumsum(np.linspace(f0, f1, n)) / SND_SR
    return np.sin(ph) * _env(n, **kw)


def _gap(dur):
    return np.zeros(int(SND_SR * dur), dtype=np.float32)


SOUNDS = {
    "rising": lambda: _chirp(520, 880, 0.16),
    "classic": lambda: _sine(900, 0.11),
    "double": lambda: np.concatenate([_sine(660, 0.07), _gap(0.03), _sine(880, 0.09)]),
    "bell": lambda: _sine(1318, 0.4, decay=9) + 0.5 * _sine(1976, 0.4, decay=12),
    "tick": lambda: _sine(1500, 0.03, fo=0.02),
    "ok": lambda: np.concatenate([_sine(700, 0.06), _sine(1000, 0.09)]),
    "fail": lambda: _sine(300, 0.18, fo=0.08),
}
_wav_cache = {}


def _wav_bytes(name, vol):
    key = (name, int(vol))
    if key not in _wav_cache:
        x = SOUNDS[name]().astype(np.float32)
        x = x / max(1e-6, float(np.max(np.abs(x))))
        x = x * (max(0, min(100, int(vol))) / 100.0) ** 1.6 * 0.9
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SND_SR)
            w.writeframes((x * 32767).astype(np.int16).tobytes())
        _wav_cache[key] = buf.getvalue()
    return _wav_cache[key]


def play_sound(name, vol=60):
    if name in (None, "", "none"):
        return
    try:
        import winsound
        if name == "system":
            winsound.MessageBeep(winsound.MB_OK)
            return
        if name not in SOUNDS:
            name = "rising"
        winsound.PlaySound(_wav_bytes(name, vol), winsound.SND_MEMORY)   # senkron: thread icinde cagrilir
    except Exception:
        pass


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
# API anahtari: Windows DPAPI ile sifrelenir (sadece bu kullanici/bilgisayar cozer)
# ----------------------------------------------------------------------------
def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    import ctypes.wintypes as wt

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inb = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    outb = BLOB()
    f = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not f(ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(outb)):
        raise OSError("DPAPI hatası")
    try:
        return ctypes.string_at(outb.pbData, outb.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(outb.pbData)


def _short(e, n=220) -> str:
    return (str(e) or repr(e))[:n]


# ----------------------------------------------------------------------------
# Kisayollar (senin tanimladigin adlandirilmis islemler: site/exe/oyun/rastgele)
# ----------------------------------------------------------------------------
def load_shortcuts():
    sc = load_json(SHORTCUTS_PATH, None)
    if isinstance(sc, list):
        for s in sc:
            s.setdefault("id", uuid.uuid4().hex[:8])
            s.setdefault("enabled", True)
        return sc
    out = []
    old = load_json(OLD_COMMANDS_PATH, [])
    skip = {"YouTube", "Google", "Spotify", "Not Defteri", "Hesap Makinesi", "Dosya Gezgini", "CS2"}
    for c in (old if isinstance(old, list) else []):   # eski 1.x komutlarindan kendi eklediklerini tasi
        if c.get("system") or not c.get("targets") or c.get("name") in skip:
            continue
        out.append({"id": c.get("id") or uuid.uuid4().hex[:8], "name": c["name"],
                    "hint": ", ".join(c.get("phrases", [])[:2]), "targets": c["targets"],
                    "mode": c.get("mode", "all"), "args": c.get("args", ""),
                    "enabled": c.get("enabled", True)})
    out.insert(0, {"id": uuid.uuid4().hex[:8], "name": "CS2", "hint": "oyun, counter strike",
                   "targets": ["steam://rungameid/730"], "mode": "all", "args": "", "enabled": True})
    save_json(SHORTCUTS_PATH, out)
    return out


def open_targets(sc, log):
    targets = sc.get("targets") or []
    if not targets:
        return {"ok": False, "error": "Bu kısayolun hedefi yok."}
    chosen = [random.choice(targets)] if sc.get("mode") == "random" else targets
    opened, errors = [], []
    for t in chosen:
        t = t.strip().strip('"')
        try:
            if sc.get("args"):
                os.startfile(t, arguments=sc["args"])
            else:
                os.startfile(t)
            opened.append(t)
        except Exception as e:
            errors.append(f"{t}: {e}")
    if opened:
        log("Açıldı: " + ", ".join(opened))
    for e in errors:
        log("Açılamadı: " + e)
    return {"ok": bool(opened), "opened": opened, "errors": errors}


# ----------------------------------------------------------------------------
# Uygulama listesi (Baslat menusu) - "spotify'i ac", "discord ac" gibi
# ----------------------------------------------------------------------------
_apps = None
_apps_lock = threading.Lock()
KNOWN_APPS = {"not defteri": "notepad.exe", "notepad": "notepad.exe", "hesap makinesi": "calc.exe",
              "calculator": "calc.exe", "dosya gezgini": "explorer.exe", "explorer": "explorer.exe",
              "görev yöneticisi": "taskmgr.exe", "task manager": "taskmgr.exe", "paint": "mspaint.exe",
              "komut istemi": "cmd.exe", "denetim masası": "control.exe", "ayarlar": "ms-settings:"}


def get_apps():
    global _apps
    with _apps_lock:
        if _apps is None:
            apps = []
            try:
                r = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                     "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                     "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
                    capture_output=True, text=True, encoding="utf-8", timeout=30, creationflags=0x08000000)
                data = json.loads(r.stdout or "[]")
                if isinstance(data, dict):
                    data = [data]
                apps = [{"name": d["Name"], "id": d["AppID"]} for d in data if d.get("Name") and d.get("AppID")]
            except Exception:
                apps = []
            _apps = apps
        return _apps


# ----------------------------------------------------------------------------
# Gemini'nin cagirabilecegi araclar
# ----------------------------------------------------------------------------
def _p(t, d, enum=None):
    p = {"type": t, "description": d}
    if enum:
        p["enum"] = enum
    return p


def _safe_url(u):
    u = (u or "").strip()
    if re.match(r"^(https?|steam|spotify|ms-settings|mailto):", u, re.I):
        return u
    if re.match(r"^[\w.-]+\.[a-z]{2,}(/\S*)?$", u, re.I):
        return "https://" + u
    return None


def t_open_url(core, a):
    u = _safe_url(a.get("url"))
    if not u:
        return {"ok": False, "error": "Geçersiz adres."}
    os.startfile(u)
    core.log(f"Site açıldı: {u}")
    return {"ok": True, "opened": u}


def t_web_search(core, a):
    import urllib.parse as up
    q = up.quote_plus(a.get("query") or "")
    site = (a.get("site") or "google").lower()
    url = {"youtube": f"https://www.youtube.com/results?search_query={q}",
           "spotify": f"https://open.spotify.com/search/{q}"}.get(site, f"https://www.google.com/search?q={q}")
    os.startfile(url)
    core.log(f"Arama: {site} / {a.get('query')}")
    return {"ok": True, "opened": url}


def t_open_app(core, a):
    name = (a.get("name") or "").strip()
    n = norm(name)
    if not n:
        return {"ok": False, "error": "Uygulama adı boş."}
    for k, v in KNOWN_APPS.items():
        if norm(k) == n:
            os.startfile(v)
            core.log(f"Uygulama açıldı: {v}")
            return {"ok": True, "opened": k}
    apps = get_apps()
    names = [norm(x["name"]) for x in apps]
    pick = None
    contains = [i for i, x in enumerate(names) if n in x or x in n]
    if contains:
        pick = min(contains, key=lambda i: len(names[i]))
    else:
        m = difflib.get_close_matches(n, names, n=1, cutoff=0.6)
        if m:
            pick = names.index(m[0])
    if pick is None:
        return {"ok": False, "error": f"'{name}' adlı uygulama bulunamadı."}
    app = apps[pick]
    subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app["id"]])
    core.log(f"Uygulama açıldı: {app['name']}")
    return {"ok": True, "opened": app["name"]}


def t_media_control(core, a):
    act = {"pause": "media_pause", "play": "media_play", "next": "next", "previous": "prev"}.get(a.get("action"))
    if not act:
        return {"ok": False, "error": "Bilinmeyen eylem."}
    msgs = []
    run_system(act, lambda m: msgs.append(m))
    for m in msgs:
        core.log(m)
    return {"ok": True, "detail": " ".join(msgs) or "Gönderildi."}


def t_set_volume(core, a):
    act = a.get("action", "up")
    try:
        amt = max(2, min(100, int(a.get("amount") or 10)))
    except Exception:
        amt = 10
    if act == "mute":
        press_key(VK["mute"])
    elif act == "up":
        press_key(VK["vol_up"], max(1, amt // 2))
    elif act == "down":
        press_key(VK["vol_down"], max(1, amt // 2))
    elif act == "set":
        press_key(VK["vol_down"], 50)
        press_key(VK["vol_up"], max(0, min(50, amt // 2)))
    else:
        return {"ok": False, "error": "Bilinmeyen eylem."}
    return {"ok": True}


def t_lock(core, a):
    run_system("lock")
    return {"ok": True}


def t_screenshot(core, a):
    run_system("screenshot")
    return {"ok": True}


def t_show_desktop(core, a):
    run_system("show_desktop")
    return {"ok": True}


def t_run_shortcut(core, a):
    name = norm(a.get("name") or "")
    for s in core.shortcuts:
        if s.get("enabled", True) and norm(s["name"]) == name:
            return open_targets(s, core.log)
    return {"ok": False, "error": "Böyle bir kısayol yok."}


TR_DAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def t_datetime(core, a):
    n = datetime.datetime.now()
    return {"ok": True, "tarih": n.strftime("%d.%m.%Y"), "saat": n.strftime("%H:%M"), "gun": TR_DAYS[n.weekday()]}


def t_timer(core, a):
    try:
        sec = max(1, min(86400, int(a.get("seconds") or 60)))
    except Exception:
        sec = 60
    label = a.get("label") or "Zamanlayıcı"

    def fire():
        core.log(f"Zamanlayıcı doldu: {label}")
        play_sound("bell", core.cfg.get("beep_volume", 60))
        if core.notify:
            core.notify("Hey Tank", f"{label} süresi doldu")
    threading.Timer(sec, fire).start()
    core.log(f"Zamanlayıcı kuruldu: {sec} sn ({label})")
    return {"ok": True, "seconds": sec}


def t_end(core, a):
    core.live.request_end()
    return {"ok": True}


def t_close_app(core, a):
    name = re.sub(r"[^\w. -]", "", a.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "Ad boş."}
    if not name.lower().endswith(".exe"):
        name += ".exe"
    r = subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True, text=True,
                       timeout=15, creationflags=0x08000000)
    return {"ok": r.returncode == 0, "output": (r.stdout or r.stderr)[:300]}


def t_powershell(core, a):
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-Command", a.get("script") or ""], capture_output=True, text=True,
                       timeout=25, creationflags=0x08000000)
    return {"ok": r.returncode == 0, "output": ((r.stdout or "") + (r.stderr or ""))[:1500]}


TOOL_DEFS = [
    dict(name="open_url", label="Web sitesi aç", danger=False, fn=t_open_url,
         desc="Verilen web adresini tarayıcıda açar (youtube.com, spotify, steam:// vb.).",
         params={"url": _p("STRING", "Açılacak adres, örn. https://www.youtube.com")}, req=["url"]),
    dict(name="web_search", label="Arama yap", danger=False, fn=t_web_search,
         desc="Google, YouTube veya Spotify'da arama yapar.",
         params={"query": _p("STRING", "Aranacak ifade"),
                 "site": _p("STRING", "Nerede aranacak", ["google", "youtube", "spotify"])}, req=["query"]),
    dict(name="open_app", label="Uygulama aç", danger=False, fn=t_open_app,
         desc="Bilgisayardaki bir uygulamayı adıyla açar (Spotify, Discord, Not Defteri, Chrome...).",
         params={"name": _p("STRING", "Uygulamanın adı")}, req=["name"]),
    dict(name="media_control", label="Müzik / medya kontrolü", danger=False, fn=t_media_control,
         desc="Çalan müziği/videoyu durdurur, başlatır veya parça değiştirir. Küçültülmüş uygulamalarda da çalışır.",
         params={"action": _p("STRING", "Yapılacak işlem", ["pause", "play", "next", "previous"])}, req=["action"]),
    dict(name="set_volume", label="Ses seviyesi", danger=False, fn=t_set_volume,
         desc="Bilgisayar sesini artırır, azaltır, kapatır veya belirli bir yüzdeye ayarlar.",
         params={"action": _p("STRING", "İşlem", ["up", "down", "mute", "set"]),
                 "amount": _p("INTEGER", "Yüzde (up/down için değişim, set için hedef seviye 0-100)")}, req=["action"]),
    dict(name="lock_computer", label="Bilgisayarı kilitle", danger=False, fn=t_lock,
         desc="Windows oturumunu kilitler.", params={}, req=[]),
    dict(name="take_screenshot", label="Ekran alıntısı", danger=False, fn=t_screenshot,
         desc="Ekran alıntısı aracını açar (Win+Shift+S).", params={}, req=[]),
    dict(name="show_desktop", label="Masaüstünü göster", danger=False, fn=t_show_desktop,
         desc="Tüm pencereleri küçültüp masaüstünü gösterir.", params={}, req=[]),
    dict(name="run_shortcut", label="Kayıtlı kısayollar", danger=False, fn=t_run_shortcut,
         desc="Kullanıcının kendi tanımladığı kısayolu çalıştırır (oyun, özel site listesi, program).",
         params={"name": _p("STRING", "Kısayolun adı")}, req=["name"]),
    dict(name="get_datetime", label="Tarih ve saat", danger=False, fn=t_datetime,
         desc="Şu anki tarih, saat ve günü verir.", params={}, req=[]),
    dict(name="set_timer", label="Zamanlayıcı", danger=False, fn=t_timer,
         desc="Belirli saniye sonra sesli ve bildirimli hatırlatma kurar.",
         params={"seconds": _p("INTEGER", "Kaç saniye sonra"), "label": _p("STRING", "Hatırlatma metni")},
         req=["seconds"]),
    dict(name="end_conversation", label="Konuşmayı bitir", danger=False, fn=t_end,
         desc="Kullanıcı işini bitirince (teşekkürler, tamam, kapat) konuşma oturumunu kapatır.",
         params={}, req=[]),
    dict(name="close_app", label="Uygulama kapat (onay ister)", danger=True, fn=t_close_app,
         desc="Çalışan bir programı kapatır (işlem adıyla, örn. spotify.exe). Kaydedilmemiş veri kaybolabilir.",
         params={"name": _p("STRING", "İşlem adı")}, req=["name"]),
    dict(name="run_powershell", label="Gelişmiş: PowerShell komutu (onay ister)", danger=True, fn=t_powershell,
         desc="Başka hiçbir araç uymuyorsa Windows PowerShell komutu çalıştırır. Her seferinde kullanıcı onaylar.",
         params={"script": _p("STRING", "Çalıştırılacak PowerShell komutu")}, req=["script"]),
]
TOOLS = {t["name"]: t for t in TOOL_DEFS}


def build_declarations(core):
    out = []
    for t in TOOL_DEFS:
        if not core.tool_enabled(t["name"]):
            continue
        params = dict(t["params"])
        desc = t["desc"]
        if t["name"] == "run_shortcut":
            sc = [s for s in core.shortcuts if s.get("enabled", True)]
            if not sc:
                continue
            params = {"name": _p("STRING", "Kısayolun adı", [s["name"] for s in sc])}
            desc += " Kısayollar: " + "; ".join(
                s["name"] + (f" ({s['hint']})" if s.get("hint") else "") for s in sc)
        d = {"name": t["name"], "description": desc}
        if params:
            d["parameters"] = {"type": "OBJECT", "properties": params, "required": t["req"]}
        out.append(d)
    return out


def run_tool(core, name, args):
    t = TOOLS.get(name)
    if not t or not core.tool_enabled(name):
        core.log(f"Araç reddedildi: {name}")
        return {"ok": False, "error": "Bu araç kapalı veya yok."}
    try:
        if t["danger"]:
            detail = args.get("script") or args.get("name") or json.dumps(args, ensure_ascii=False)
            if not core.confirm(t["label"], str(detail)):
                core.log(f"Onaylanmadı: {name}")
                return {"ok": False, "error": "Kullanıcı onaylamadı."}
        res = t["fn"](core, args)
        core.log(f"Araç: {name} {json.dumps(args, ensure_ascii=False)[:120]}")
        core.fb("ok" if res.get("ok") else "fail")
        return res
    except Exception as e:
        core.log(f"Araç hatası ({name}): {_short(e)}")
        core.fb("fail")
        return {"ok": False, "error": _short(e)}


def system_prompt(core):
    sc = "; ".join(s["name"] + (f" ({s['hint']})" if s.get("hint") else "")
                   for s in core.shortcuts if s.get("enabled", True)) or "yok"
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    return ("Sen 'Tank' adlı, kullanıcının Windows bilgisayarını sesle yöneten Türkçe bir asistansın. "
            "Her zaman Türkçe konuş. Yanıtların çok kısa olsun (en fazla 1-2 cümle), samimi ama net. "
            "Kullanıcı bilgisayarda bir şey yapmanı isterse ilgili aracı çağır; aracı çağırmadan 'yaptım' deme, "
            "aracın sonucuna göre kısaca bildir. Araç hata verirse dürüstçe söyle. "
            "'Hey Tank' uyandırma kelimesidir, onu yok say. Anlamazsan kısaca tekrar iste. "
            "Silme, kapatma gibi riskli işlerde önce sözlü onay iste. "
            "Kullanıcı işini bitirince (teşekkürler, tamam, kapat gibi) end_conversation aracını çağır. "
            f"Kayıtlı kısayollar: {sc}. Şu an: {now}.")


def rank_live_models(names):
    """Live modellerini tercih sirasina koyar: 3.8 live > diger 3.x live > native-audio (yeni tarih once)."""
    groups = {}
    for n in names:
        l = n.lower()
        if "translate" in l or "tts" in l:
            continue
        if "3.8" in l and "live" in l:
            r = 0
        elif "live" in l and "gemini-3" in l:
            r = 1
        elif "native-audio" in l:
            r = 2
        elif "live" in l:
            r = 3
        else:
            r = 5          # bidiGenerateContent destekliyor ama adi bilinmiyor: en sona koy
        groups.setdefault(r, []).append(n)
    out = []
    for r in sorted(groups):
        out += sorted(groups[r], reverse=True)
    return out


def list_live_models(key):
    from google import genai
    client = genai.Client(api_key=key)
    live, allm = [], []
    for m in client.models.list():
        n = (m.name or "").replace("models/", "")
        allm.append(n)
        if "bidiGenerateContent" in (getattr(m, "supported_actions", None) or []):
            live.append(n)
    if not live:
        live = [n for n in allm if "live" in n.lower() or "native-audio" in n.lower()]
    return {"live": live, "all": allm}


LIVE_FALLBACK_MODELS = ["gemini-3.8-live", "gemini-3.1-flash-live-preview",
                        "gemini-2.5-flash-native-audio-preview-12-2025",
                        "gemini-2.5-flash-native-audio-preview-09-2025",
                        "gemini-live-2.5-flash-preview", "gemini-2.0-flash-live-001"]


# ----------------------------------------------------------------------------
# Cekirdek: ayarlar, kayit, durum
# ----------------------------------------------------------------------------
class Core:
    def __init__(self):
        raw = load_json(CONFIG_PATH, {})
        self.cfg = dict(DEFAULT_CONFIG)
        for k in DEFAULT_CONFIG:
            if k in raw:
                self.cfg[k] = raw[k]
        if not isinstance(self.cfg.get("tools"), dict):
            self.cfg["tools"] = {}
        if self.cfg.get("live_model") == LIVE_MODEL_LEGACY:
            self.cfg["live_model"] = ""      # eski model 404 veriyor olabilir -> otomatik sec
        if raw.get("cfg_ver", 0) < 3:       # 1.x -> 2.0: eski motor ayarlari atilir
            self.cfg["cfg_ver"] = 3
            save_json(CONFIG_PATH, self.cfg)
        self.shortcuts = load_shortcuts()
        self.lock = threading.Lock()
        self.logs = deque(maxlen=400)
        self.seq = 0
        self.chat = []
        self.state = {"state": "loading", "status": "Başlatılıyor...", "level": 0.0, "level_out": 0.0,
                      "heard": "", "confirm": None, "beat": time.time()}
        self._confirm_evt = threading.Event()
        self._confirm_ok = False
        self._confirm_id = None
        self.attention = None     # App: pencereyi one getir
        self.notify = None        # App: tepsi bildirimi
        self.engine = Engine(self)
        self.live = Live(self)

    def log(self, msg):
        with self.lock:
            self.seq += 1
            self.logs.append((self.seq, f"[{datetime.datetime.now():%H:%M:%S}] {msg}"))

    def set_state(self, state, status):
        self.state["state"] = state
        self.state["status"] = status

    def set_chat(self, role, text):
        with self.lock:
            if self.chat and self.chat[-1]["role"] == role and not self.chat[-1]["closed"]:
                self.chat[-1]["text"] = text
            else:
                self.chat.append({"role": role, "text": text, "closed": False})
                del self.chat[:-30]

    def close_chat(self):
        with self.lock:
            if self.chat:
                self.chat[-1]["closed"] = True

    def save_cfg(self):
        save_json(CONFIG_PATH, self.cfg)

    def save_shortcuts(self):
        save_json(SHORTCUTS_PATH, self.shortcuts)

    def set_key(self, key, portable=False):
        key = (key or "").strip()
        if not key:
            self.cfg["api_key_enc"] = ""
        elif portable:      # klasor baska bilgisayara tasinacaksa sifrelemeden sakla
            self.cfg["api_key_enc"] = "plain:" + base64.b64encode(key.encode()).decode()
        else:
            try:
                self.cfg["api_key_enc"] = "dpapi:" + base64.b64encode(_dpapi(key.encode())).decode()
            except Exception:
                self.cfg["api_key_enc"] = "plain:" + base64.b64encode(key.encode()).decode()
        self.save_cfg()

    def get_key(self):
        v = self.cfg.get("api_key_enc", "")
        try:
            if v.startswith("dpapi:"):
                return _dpapi(base64.b64decode(v[6:]), True).decode()
            if v.startswith("plain:"):
                return base64.b64decode(v[6:]).decode()
        except Exception:
            pass
        return ""

    def tool_enabled(self, name):
        t = TOOLS.get(name)
        if not t:
            return False
        return bool(self.cfg["tools"].get(name, not t["danger"]))

    def talk(self):
        """Daireye tik / Ctrl+Alt+T: oturum yoksa baslat, varsa kapat."""
        if self.live.active:
            self.live.request_stop()
        else:
            self.engine.manual.set()

    def fb(self, name):
        if self.cfg.get("feedback_sounds", True) and self.cfg.get("beep_sound", "rising") != "none":
            threading.Thread(target=play_sound, args=(name, self.cfg.get("beep_volume", 60)), daemon=True).start()

    def confirm(self, title, detail, timeout=30):
        cid = uuid.uuid4().hex[:6]
        self._confirm_evt.clear()
        self._confirm_ok = False
        self._confirm_id = cid
        self.state["confirm"] = {"id": cid, "title": title, "detail": detail}
        if self.attention:
            try:
                self.attention()
            except Exception:
                pass
        got = self._confirm_evt.wait(timeout)
        self.state["confirm"] = None
        return bool(got and self._confirm_ok)

    def answer_confirm(self, cid, ok):
        if cid == self._confirm_id:
            self._confirm_ok = bool(ok)
            self._confirm_evt.set()


# ----------------------------------------------------------------------------
# Uyandirma motoru ("Hey Tank") - kucuk Vosk modeli, sadece bunun icin
# ----------------------------------------------------------------------------
class Engine(threading.Thread):
    def __init__(self, core):
        super().__init__(daemon=True)
        self.core = core
        self.stop = threading.Event()
        self.reopen = threading.Event()
        self.manual = threading.Event()
        self.models = {}
        self.last_use = time.time()

    def _hotkey_loop(self):
        """Ctrl+Alt+T: uyandirma kelimesi olmadan dinlemeyi baslatir."""
        try:
            import ctypes.wintypes as wt
            u = ctypes.windll.user32
            if not u.RegisterHotKey(None, 1, 0x2 | 0x1 | 0x4000, 0x54):
                self.core.log("Kısayol (Ctrl+Alt+T) kaydedilemedi; başka bir uygulama kullanıyor olabilir.")
                return
            msg = wt.MSG()
            while not self.stop.is_set():
                if u.PeekMessageW(ctypes.byref(msg), None, 0x312, 0x312, 1):
                    if self.core.cfg.get("hotkey", True):
                        self.core.talk()
                else:
                    time.sleep(0.05)
        except Exception as e:
            self.core.log(f"Kısayol başlatılamadı: {e!r}")

    def run(self):
        threading.Thread(target=self._hotkey_loop, daemon=True).start()
        while not self.stop.is_set():
            try:
                self.session()
            except Exception as e:
                self.core.log(f"Motor hatası: {_short(e)} - 3 sn sonra yeniden denenecek.")
                self.core.set_state("error", "Hata - yeniden deneniyor")
                self.stop.wait(3)

    def get_model(self, name):
        from vosk import Model
        d = MODELS / ("vosk-tr" if name == "tr" else "vosk-en")
        if not d.exists():
            return None
        if name not in self.models:
            self.core.set_state("loading", "Model yükleniyor...")
            self.models[name] = Model(str(d))
        return self.models[name]

    @staticmethod
    def flush(aq):
        while not aq.empty():
            try:
                aq.get_nowait()
            except queue.Empty:
                break

    def start_live(self, aq, rec, noise):
        core = self.core
        thr = float(min(2500.0, max(300.0, np.median(noise) * 2.5))) if len(noise) else 300.0
        if rec is not None:
            rec.Reset()
        if core.cfg.get("beep_sound", "rising") != "none":
            threading.Thread(target=play_sound, args=(core.cfg.get("beep_sound", "rising"),
                             core.cfg.get("beep_volume", 60)), daemon=True).start()
        try:
            core.live.run(aq, thr)
        except Exception as e:
            core.log(f"Oturum hatası: {_short(e)}")
        self.flush(aq)
        if rec is not None:
            rec.Reset()
        self.last_use = time.time()

    def session(self):
        import sounddevice as sd
        core, cfg = self.core, self.core.cfg
        self.reopen.clear()

        rec, strict = None, True
        wake_model = None
        wake = cfg["wake_lang"] if cfg["wake_lang"] in WAKE_GRAMMAR else "tr"
        sens = cfg.get("wake_sens", "normal")
        if cfg.get("wake_mode", "vosk") == "off":
            self.models.clear()
            gc.collect()
            core.log("“Hey Tank” dinleme kapalı: daireye dokun veya Ctrl+Alt+T.")
        else:
            try:
                from vosk import KaldiRecognizer, SetLogLevel
                SetLogLevel(-1)
                for k in list(self.models):
                    if k != wake:
                        del self.models[k]
                gc.collect()
                wake_model = self.get_model(wake)
            except Exception as e:
                core.log(f"Uyandırma modeli yüklenemedi: {_short(e)}")
                wake_model = None
        if wake_model is not None:
            phrases, strict = build_wake(wake_model, wake, cfg.get("strict_wake", True),
                                         None if sens == "sensitive" else 2)
            if strict:
                core.log("Uyandırma: “hey tank” (hey + tank birlikte).")
            elif cfg.get("strict_wake", True):
                core.log("Uyarı: model “hey” kelimesini bilmiyor, yalnızca “tank” ile uyanıyor.")
            rec = KaldiRecognizer(wake_model, SR, json.dumps(phrases + ["[unk]"], ensure_ascii=False))
            rec.SetWords(True)
            core.log(f"Uyandırma hassasiyeti: {sens}")
        elif cfg.get("wake_mode", "vosk") != "off":
            core.log("Uyandırma modeli yok: daireye tıkla veya Ctrl+Alt+T kullan.")

        aq = queue.Queue()

        def cb(indata, frames, t, status):
            aq.put(bytes(indata))

        noise = deque(maxlen=80)
        recent = deque(maxlen=8)
        hits = 0
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

                if self.manual.is_set():
                    self.manual.clear()
                    core.log("Elle başlatıldı (daire / kısayol).")
                    self.start_live(aq, rec, noise)
                    last, last_data, hits = None, time.time(), 0
                    continue
                listening = cfg.get("listening", True) and rec is not None
                if not listening:
                    if last != "pause":
                        core.set_state("pause", "Dinleme kapalı" if rec is not None else "Daireye dokun veya Ctrl+Alt+T")
                        if rec is not None:
                            rec.Reset()
                        last = "pause"
                    continue
                if last != "wait":
                    core.set_state("wait", "“Hey Tank” bekleniyor")
                    last = "wait"

                noise.append(rms)
                recent.append(rms)
                conf = None
                final = rec.AcceptWaveform(data)
                if final:
                    res = json.loads(rec.Result())
                    txt = res.get("text", "")
                    ws = res.get("result") or []
                    conf = min((w.get("conf", 0.0) for w in ws), default=0.0)
                else:
                    txt = json.loads(rec.PartialResult()).get("partial", "")
                if txt and txt != "[unk]":
                    core.state["heard"] = txt
                sens = cfg.get("wake_sens", "normal")
                fire = False
                if is_wake(txt, strict):
                    voiced = max(recent) >= max(300.0, float(np.median(noise)) * 2.0)
                    cooled = time.time() - self.last_use > 3.0
                    if voiced and cooled:
                        if sens == "sensitive":
                            fire = True
                        elif sens == "normal":
                            hits += 1
                            fire = hits >= 2 or (final and (conf or 0.0) >= 0.5)
                        else:
                            fire = bool(final and (conf or 0.0) >= 0.75)
                else:
                    hits = 0
                if fire:
                    hits = 0
                    core.log(f"Uyandırma: “{txt}”" + (f" (güven {conf:.2f})" if conf is not None else ""))
                    self.start_live(aq, rec, noise)
                    last, last_data = None, time.time()


# ----------------------------------------------------------------------------
# Gemini Live oturumu: mikrofon -> Gemini -> (araclar) -> ses cikisi
# ----------------------------------------------------------------------------
class Live:
    def __init__(self, core):
        self.core = core
        self.active = False
        self.end_flag = False
        self.stop_flag = False
        self.speaking = False
        self.tool_busy = 0
        self.last_audio = 0.0
        self.last_voice = 0.0
        self.last_model = 0.0
        self.pq = queue.Queue()
        self._play_stop = False
        self.play_thread = None
        self._connected = False

    def request_end(self):
        self.end_flag = True

    def request_stop(self):
        self.stop_flag = True

    def _drain(self):
        while True:
            try:
                self.pq.get_nowait()
            except queue.Empty:
                break

    def run(self, aq, thr):
        core = self.core
        if self.active:
            return
        self.active = True
        self.end_flag = self.stop_flag = self.speaking = False
        self.tool_busy = 0
        self.last_voice = self.last_model = time.time()
        try:
            asyncio.run(self._main(aq, thr))
        except Exception as e:
            core.log(f"Gemini hatası: {_short(e)}")
            core.set_state("error", "Gemini hatası - Kayıt sekmesine bak")
            core.fb("fail")
            time.sleep(2)
        finally:
            self.active = False
            self.speaking = False
            self._drain()
            core.state["level_out"] = 0.0
            core.close_chat()

    def _config(self):
        core, cfg = self.core, self.core.cfg
        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": system_prompt(core),
            "input_audio_transcription": {},
            "output_audio_transcription": {},
            "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": cfg.get("voice", "Charon")}}},
        }
        decls = build_declarations(core)
        if decls:
            config["tools"] = [{"function_declarations": decls}]
        return config

    async def _main(self, aq, thr):
        key = self.core.get_key()
        if not key:
            self.core.log("Gemini API anahtarı yok. Ayarlar > Gemini bölümüne ekle.")
            self.core.set_state("error", "Gemini anahtarı gerekli")
            self.core.fb("fail")
            time.sleep(1.5)
            return
        await self.connect_loop(key, aq, thr, probe=False)

    async def connect_loop(self, key, aq, thr, probe=False):
        """Model ve API surumu birlestirmelerini dener (404/desteklenmiyor -> siradaki); calisani kaydeder."""
        from google import genai
        from google.genai import types
        core, cfg = self.core, self.core.cfg
        clients = {}

        def client_for(ver):
            if ver not in clients:
                clients[ver] = (genai.Client(api_key=key) if not ver else
                                genai.Client(api_key=key, http_options=types.HttpOptions(api_version=ver)))
            return clients[ver]

        config = self._config()
        versions = []
        for v in ((cfg.get("api_version") or ""), "", "v1beta", "v1alpha"):
            if v not in versions:
                versions.append(v)
        first = (cfg.get("live_model") or "").strip()
        todo, tried, errors, listed = ([first] if first else []), [], [], False
        good_ver, hs404 = None, 0
        while len(tried) < 8:
            if not todo:
                if listed:
                    break
                listed = True
                core.set_state("connecting", "Model aranıyor...")
                try:
                    info = await asyncio.to_thread(list_live_models, key)
                except Exception as e:
                    info = {"live": [], "all": []}
                    errors.append("model listesi alınamadı: " + _short(e, 200))
                    core.log(errors[-1])
                core.log(f"Model listesi: {len(info['all'])} model; Live olanlar: "
                         + (", ".join(info["live"][:6]) or "yok"))
                cand = rank_live_models(info["live"]) + LIVE_FALLBACK_MODELS
                todo = [m for i, m in enumerate(cand) if m not in tried and m not in cand[:i]]
                if not todo:
                    break
            model = todo.pop(0)
            if model in tried:
                continue
            tried.append(model)
            all404 = True
            for ver in ([good_ver] if good_ver is not None else versions):
                self._connected = False
                t0 = time.time()
                try:
                    await self._session(client_for(ver), model, config, aq, thr, types, probe)
                    if cfg.get("live_model") != model or (cfg.get("api_version") or "") != ver:
                        cfg["live_model"], cfg["api_version"] = model, ver
                        core.save_cfg()
                        core.log(f"Bağlantı ayarı kaydedildi: model={model}, sürüm={ver or 'varsayılan'}")
                    return model
                except Exception as e:
                    low = str(e).lower()
                    if self._connected and time.time() - t0 > 4:
                        raise                                  # oturum calisirken kopma: yeniden deneme yok
                    errors.append(f"{model} [{ver or 'varsayılan'}]: {_short(e, 150)}")
                    core.log("Bağlanamadı: " + errors[-1])
                    if self._connected:
                        good_ver = ver                         # el sikisma tamam, sorun modelde
                    handshake404 = (not self._connected) and "404" in low
                    modelish = any(x in low for x in ("not found", "not supported", "unsupported", "1008"))
                    if handshake404:
                        continue                               # siradaki API surumu
                    all404 = False
                    if modelish:
                        break                                  # siradaki model
                    raise RuntimeError(_short(e, 400))         # anahtar/kota/bolge gibi gercek hata
            if all404:
                hs404 += 1
                if hs404 >= 2:
                    break                                      # model degistirmek yardim etmiyor
        raise RuntimeError("Live bağlantısı kurulamadı. " + " | ".join(errors[-4:]))

    async def _session(self, client, model, config, aq, thr, types, probe):
        core = self.core
        core.set_state("connecting", "Bağlanıyor...")
        async with client.aio.live.connect(model=model, config=config) as session:
            self._connected = True
            if probe:
                await asyncio.sleep(0.7)
                return
            core.log(f"Gemini oturumu açıldı ({model}).")
            core.set_state("listen", "Dinliyorum")
            self._start_player()
            try:
                t_send = asyncio.create_task(self._send(session, aq, thr, types))
                t_recv = asyncio.create_task(self._recv(session, types))
                done, pending = await asyncio.wait({t_send, t_recv}, return_when=asyncio.FIRST_COMPLETED)
                for p in pending:
                    p.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for d in done:
                    d.result()
            finally:
                self._stop_player()
        core.log("Oturum kapandı.")

    async def _send(self, session, aq, thr, types):
        core, cfg = self.core, self.core.cfg
        idle = float(cfg.get("idle_sec", 12))
        barge = bool(cfg.get("barge_in", False))
        while not core.engine.stop.is_set():
            if self.stop_flag:
                core.log("Oturum elle kapatıldı.")
                return
            busy = self.speaking or not self.pq.empty() or self.tool_busy > 0
            if self.end_flag and not busy:
                return
            now = time.time()
            quiet = now - max(self.last_voice, self.last_model, self.last_audio)
            if (not busy and quiet > idle) or quiet > idle + 90:     # 2. kosul: takilmaya karsi emniyet
                core.log("Konuşma olmadığı için oturum kapandı.")
                return
            try:
                data = await asyncio.to_thread(aq.get, True, 0.2)
            except queue.Empty:
                continue
            a = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(a ** 2)))
            core.state["level"] = min(1.0, rms / 3000.0)
            core.state["beat"] = now
            if busy and not barge:          # Tank konusurken mikrofonu gonderme (yanki olmasin)
                continue
            if rms > thr:
                self.last_voice = now
            await session.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))

    async def _recv(self, session, types):
        core, cfg = self.core, self.core.cfg
        ubuf = mbuf = ""
        while True:
            async for resp in session.receive():
                core.state["beat"] = time.time()
                sc = getattr(resp, "server_content", None)
                if sc:
                    if getattr(sc, "interrupted", False):
                        self._drain()
                        self.speaking = False
                    mt = getattr(sc, "model_turn", None)
                    if mt and mt.parts:
                        for part in mt.parts:
                            d = getattr(part, "inline_data", None)
                            if d and d.data and cfg.get("speak", True):
                                self.speaking = True
                                self.last_model = time.time()
                                self.pq.put(bytes(d.data))
                    it = getattr(sc, "input_transcription", None)
                    if it and it.text:
                        ubuf += it.text
                        core.set_chat("user", ubuf.strip())
                        self.last_voice = time.time()
                    ot = getattr(sc, "output_transcription", None)
                    if ot and ot.text:
                        mbuf += ot.text
                        core.set_chat("model", mbuf.strip())
                        self.last_model = time.time()
                    if getattr(sc, "turn_complete", False):
                        ubuf = mbuf = ""
                        core.close_chat()
                        if not self.speaking and self.pq.empty():
                            core.set_state("listen", "Dinliyorum")
                tc = getattr(resp, "tool_call", None)
                if tc:
                    self.tool_busy += 1
                    try:
                        await self._tools(session, tc, types)
                    finally:
                        self.tool_busy -= 1
                if getattr(resp, "go_away", None):
                    core.log("Gemini: bağlantı yakında kapanacak.")

    async def _tools(self, session, tc, types):
        core = self.core
        resps = []
        for fc in tc.function_calls:
            args = dict(fc.args or {})
            label = TOOLS[fc.name]["label"] if fc.name in TOOLS else fc.name
            core.set_state("think", f"Çalıştırıyor: {label}")
            res = await asyncio.to_thread(run_tool, core, fc.name, args)
            resps.append(types.FunctionResponse(id=fc.id, name=fc.name, response={"result": res}))
        await session.send_tool_response(function_responses=resps)
        self.last_model = time.time()
        core.set_state("listen", "Dinliyorum")

    def _start_player(self):
        self._play_stop = False
        self.play_thread = threading.Thread(target=self._play_loop, daemon=True)
        self.play_thread.start()

    def _stop_player(self):
        self._play_stop = True
        self._drain()
        if self.play_thread:
            self.play_thread.join(timeout=2)

    def _play_loop(self):
        core = self.core
        try:
            import sounddevice as sd
            with sd.RawOutputStream(samplerate=OUT_SR, channels=1, dtype="int16",
                                    device=core.cfg.get("out_device")) as out:
                while not self._play_stop:
                    try:
                        chunk = self.pq.get(timeout=0.1)
                    except queue.Empty:
                        if self.speaking and time.time() - self.last_audio > 0.5:
                            self.speaking = False
                            core.state["level_out"] = 0.0
                            if self.active:
                                core.set_state("listen", "Dinliyorum")
                        continue
                    self.speaking = True
                    core.set_state("speak", "Konuşuyor")
                    x = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
                    core.state["level_out"] = min(1.0, float(np.sqrt(np.mean(x ** 2))) / 6000.0)
                    out.write(chunk)
                    self.last_audio = time.time()
        except Exception as e:
            core.log(f"Ses çıkışı hatası: {_short(e)}")
            self.speaking = False


# ----------------------------------------------------------------------------
# JS <-> Python koprusu (alt cizgili ozellikler pywebview'a gorunmez)
# ----------------------------------------------------------------------------
INT_KEYS = {"beep_volume", "idle_sec"}
BOOL_KEYS = {"speak", "barge_in", "listening", "strict_wake", "hotkey", "feedback_sounds", "onboarded"}
STR_KEYS = {"live_model", "voice", "wake_lang", "wake_sens", "beep_sound", "wake_mode"}


class Api:
    def __init__(self, core, app):
        self._core = core
        self._app = app

    def init(self):
        c = self._core
        mics, outs = [{"id": None, "name": "Varsayılan"}], [{"id": None, "name": "Varsayılan"}]
        try:
            import sounddevice as sd
            ha = sd.default.hostapi
            for i, d in enumerate(sd.query_devices()):
                if d["hostapi"] != ha:
                    continue
                if d["max_input_channels"] > 0:
                    mics.append({"id": i, "name": d["name"]})
                if d["max_output_channels"] > 0:
                    outs.append({"id": i, "name": d["name"]})
        except Exception:
            pass
        return {"shortcuts": c.shortcuts, "config": self._public_cfg(), "mics": mics, "outs": outs,
                "voices": VOICES, "tools": self._tools(), "autostart": get_autostart(),
                "version": VERSION, "has_key": bool(c.get_key())}

    def _public_cfg(self):
        return {k: v for k, v in self._core.cfg.items() if k not in ("api_key_enc", "tools", "cfg_ver")}

    def _tools(self):
        c = self._core
        return [{"name": t["name"], "label": t["label"], "desc": t["desc"], "danger": t["danger"],
                 "enabled": c.tool_enabled(t["name"])} for t in TOOL_DEFS]

    def poll(self, since):
        c = self._core
        with c.lock:
            logs = [l for l in c.logs if l[0] > since]
            chat = [{"role": m["role"], "text": m["text"]} for m in c.chat[-6:]]
        s = dict(c.state)
        age = time.time() - s.pop("beat", time.time())
        busy = s["state"] in ("loading", "connecting", "think", "listen", "speak")
        s["stale"] = age > (180 if busy else 15)
        s["logs"] = [l[1] for l in logs]
        s["seq"] = logs[-1][0] if logs else since
        s["chat"] = chat
        s["live"] = c.live.active
        s["has_key"] = bool(c.cfg.get("api_key_enc"))
        return s

    # --- konusma
    def talk(self):
        self._core.talk()
        return True

    def confirm_answer(self, cid, ok):
        self._core.answer_confirm(cid, ok)
        return True

    # --- Gemini
    def set_key(self, key, portable=False):
        self._core.set_key(key, bool(portable))
        return bool(self._core.get_key())

    def open_external(self, url):
        if isinstance(url, str) and url.startswith("https://aistudio.google.com/"):
            os.startfile(url)
            return True
        return False

    def test_live(self):
        """Gercek bir Live baglantisi acip kapatir: anahtar + model + arac semalari dogrulanir."""
        c = self._core
        key = c.get_key()
        if not key:
            return {"ok": False, "error": "Önce anahtarı kaydet."}
        try:
            model = asyncio.run(c.live.connect_loop(key, None, 0.0, probe=True))
            c.set_state("wait", "“Hey Tank” bekleniyor")
            return {"ok": True, "model": model}
        except Exception as e:
            return {"ok": False, "error": _short(e, 600)}

    def check_key(self, key=""):
        key = (key or "").strip() or self._core.get_key()
        if not key:
            return {"ok": False, "error": "Anahtar boş."}
        try:
            info = list_live_models(key)
            return {"ok": True, "models": rank_live_models(info["live"]), "count": len(info["all"])}
        except Exception as e:
            return {"ok": False, "error": _short(e, 600)}

    # --- kisayollar
    def save_shortcut(self, sc):
        c = self._core
        sc["targets"] = [t.strip() for t in sc.get("targets", []) if t.strip()]
        if not sc.get("id"):
            sc["id"] = uuid.uuid4().hex[:8]
            sc["enabled"] = True
            c.shortcuts.append(sc)
        else:
            for i, x in enumerate(c.shortcuts):
                if x["id"] == sc["id"]:
                    c.shortcuts[i] = sc
        c.save_shortcuts()
        return c.shortcuts

    def delete_shortcut(self, sid):
        c = self._core
        c.shortcuts[:] = [x for x in c.shortcuts if x["id"] != sid]
        c.save_shortcuts()
        return c.shortcuts

    def toggle_shortcut(self, sid, enabled):
        for x in self._core.shortcuts:
            if x["id"] == sid:
                x["enabled"] = bool(enabled)
        self._core.save_shortcuts()
        return True

    def test_shortcut(self, sid):
        for x in self._core.shortcuts:
            if x["id"] == sid:
                threading.Thread(target=open_targets, args=(x, self._core.log), daemon=True).start()
        return True

    # --- araclar / ayarlar
    def set_tool(self, name, enabled):
        if name in TOOLS:
            self._core.cfg["tools"][name] = bool(enabled)
            self._core.save_cfg()
        return self._tools()

    def set_config(self, key, value):
        c = self._core
        if key not in DEFAULT_CONFIG or key in ("api_key_enc", "tools", "cfg_ver"):
            return False
        if key in ("mic_device", "out_device"):
            value = None if value in (None, "") else int(value)
        elif key in INT_KEYS:
            value = int(value)
        elif key in BOOL_KEYS:
            value = bool(value)
        elif key in STR_KEYS:
            value = str(value)
        c.cfg[key] = value
        c.save_cfg()
        if key in ("mic_device", "wake_lang", "strict_wake", "wake_sens", "wake_mode"):
            c.engine.reopen.set()
        return True

    def test_beep(self, name, vol):
        threading.Thread(target=play_sound, args=(name, int(vol)), daemon=True).start()
        return True

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
        import tkinter
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
        from google import genai  # noqa: F401
        from google.genai import types  # noqa: F401
        import websockets  # noqa: F401
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

    def notify(self, title, msg):
        try:
            if self.tray:
                self.tray.notify(msg, title)
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
        self.core.attention = self.show
        self.core.notify = self.notify
        threading.Thread(target=get_apps, daemon=True).start()   # Baslat menusu listesini onceden hazirla
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
