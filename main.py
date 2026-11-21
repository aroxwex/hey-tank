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
import shutil
import subprocess
import http.server
import io
import wave
from collections import deque
from pathlib import Path

import numpy as np

VERSION = "2.2"
SR = 16000
OUT_SR = 24000


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE = base_dir()
RES = Path(getattr(sys, "_MEIPASS", BASE))
UI_FILE = RES / "ui" / "index.html"
DATA = BASE / "data"
CONFIG_PATH = DATA / "config.json"
SHORTCUTS_PATH = DATA / "shortcuts.json"
MEMORY_PATH = DATA / "memory.json"
REMINDERS_PATH = DATA / "reminders.json"
LOG_PATH = DATA / "hey-tank.log"
OLD_COMMANDS_PATH = DATA / "commands.json"
MODELS = BASE / "models"

DEAD_MODELS = {"gemini-3.1-flash-live-preview", "gemini-2.5-flash-native-audio-preview-12-2025",
               "gemini-2.5-flash-native-audio-preview-09-2025", "gemini-live-2.5-flash-preview",
               "gemini-2.0-flash-live-001"}
VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede", "Zephyr", "Orus", "Leda"]

DEFAULT_CONFIG = {
    "cfg_ver": 5,
    "api_key_enc": "",
    "api_version": "",
    "live_model": "",
    "onboarded": False,
    "wake_mode": "vosk",
    "voice": "Charon",
    "idle_sec": 20,
    "barge_in": False,
    "listening": True,
    "wake_lang": "tr",
    "wake_sens": "normal",
    "strict_wake": True,
    "hotkey": True,
    "mic_device": None,
    "out_device": None,
    "beep_sound": "rising",
    "beep_volume": 60,
    "feedback_sounds": True,
    "tools": {},
    "seeded_v21": False,
    "listen_mode": "wake",
    "language": "tr",
    "custom_instructions": "",
    "use_memory": True,
    "google_search": True,
    "advanced_settings": False,
    "sidebar_collapsed": False,
    "close_to_tray": True,
    "hotkey_combo": "ctrl+alt+t",
    "weather_location": "",
    "log_to_file": True,
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


VK = {"vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD,
      "play_pause": 0xB3, "next": 0xB0, "prev": 0xB1}
EXT_VK = {0xAF, 0xAE, 0xAD, 0xB3, 0xB0, 0xB1, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}


def press_combo(vks, slow=False):
    u = ctypes.windll.user32
    for v in vks:
        u.keybd_event(v, u.MapVirtualKeyW(v, 0), 1 if v in EXT_VK else 0, 0)
        if slow:
            time.sleep(0.03)
    if slow:
        time.sleep(0.06)
    for v in reversed(vks):
        u.keybd_event(v, u.MapVirtualKeyW(v, 0), (1 if v in EXT_VK else 0) | 2, 0)
        if slow:
            time.sleep(0.02)


def press_key(vk, times=1):
    for _ in range(times):
        press_combo([vk])
        time.sleep(0.02)


KEYMAP = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "windows": 0x5B,
          "enter": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08,
          "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21,
          "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
          "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1, "mute": 0xAD,
          "volumeup": 0xAF, "volumedown": 0xAE}


def parse_keys(text):
    vks = []
    for part in re.split(r"\s*\+\s*", (text or "").strip().lower()):
        if not part:
            continue
        if part in KEYMAP:
            vks.append(KEYMAP[part])
        elif re.fullmatch(r"[a-z0-9]", part):
            vks.append(ord(part.upper()))
        elif re.fullmatch(r"f([1-9]|1\d|2[0-4])", part):
            vks.append(0x6F + int(part[1:]))
        else:
            return None
    return vks or None


def hotkey_parts(combo):
    vks = parse_keys(combo)
    if not vks:
        return 0, 0
    mod_map = {0x11: 2, 0x12: 1, 0x10: 4, 0x5B: 8}
    mods, key = 0, 0
    for v in vks:
        if v in mod_map:
            mods |= mod_map[v]
        else:
            key = v
    return (mods, key) if (mods and key) else (0, 0)


def focus_window(query):
    u = ctypes.windll.user32
    VP = ctypes.c_void_p
    u.IsWindowVisible.argtypes = [VP]
    u.GetWindowTextLengthW.argtypes = [VP]
    u.GetWindowTextW.argtypes = [VP, ctypes.c_wchar_p, ctypes.c_int]
    u.ShowWindow.argtypes = [VP, ctypes.c_int]
    u.SetForegroundWindow.argtypes = [VP]
    wins, q = [], norm(query)
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, VP, VP)

    def cb(hwnd, lp):
        if u.IsWindowVisible(hwnd):
            n = u.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, buf, n + 1)
                if q and q in norm(buf.value):
                    wins.append((hwnd, buf.value))
        return True

    u.EnumWindows(proc(cb), 0)
    if not wins:
        return None
    hwnd, title = wins[0]
    u.ShowWindow(hwnd, 9)
    u.keybd_event(0x12, 0, 0, 0)
    u.keybd_event(0x12, 0, 2, 0)
    u.SetForegroundWindow(hwnd)
    return title


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
        elif ok:
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
        winsound.PlaySound(_wav_bytes(name, vol), winsound.SND_MEMORY)
    except Exception:
        pass


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _migrate_old():
    for name in ("config.json", "shortcuts.json", "commands.json"):
        old, new = BASE / name, DATA / name
        if old.exists() and not new.exists():
            try:
                DATA.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(new))
            except Exception:
                pass


_migrate_old()

_TR = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def norm(s: str) -> str:
    s = s.replace("İ", "i").replace("I", "ı").lower().translate(_TR)
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


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
    for c in (old if isinstance(old, list) else []):
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


def run_shortcut_obj(sc, log):
    res = {"ok": True}
    if sc.get("targets"):
        res = open_targets(sc, log)
    if sc.get("focus") or sc.get("keys"):
        if sc.get("targets"):
            time.sleep(1.2)
        if sc.get("focus"):
            t = focus_window(sc["focus"])
            log(f"Pencere öne getirildi: {t}" if t else f"Pencere bulunamadı: {sc['focus']}")
            time.sleep(0.3)
        if sc.get("keys"):
            vks = parse_keys(sc["keys"])
            if vks:
                press_combo(vks, slow=True)
                log(f"Tuşlar gönderildi: {sc['keys']}")
                res = {"ok": True, "keys": sc["keys"]}
            else:
                res = {"ok": False, "error": "Tuş kombinasyonu geçersiz."}
    return res


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
            return run_shortcut_obj(s, core.log)
    return {"ok": False, "error": "Böyle bir kısayol yok."}


BLOCKED_COMBOS = [{0x12, 0x73}, {0x11, 0x12, 0x2E}, {0x5B, 0x4C}]


def t_press_keys(core, a):
    vks = parse_keys(a.get("keys"))
    if not vks or len(vks) > 5:
        return {"ok": False, "error": "Geçersiz tuş kombinasyonu. Örnek: ctrl+shift+m"}
    if any(b <= set(vks) for b in BLOCKED_COMBOS):
        return {"ok": False, "error": "Bu kombinasyon güvenlik için engelli."}
    press_combo(vks, slow=True)
    core.log(f"Tuşlar: {a.get('keys')}")
    return {"ok": True}


def t_focus_window(core, a):
    t = focus_window(a.get("title") or "")
    if not t:
        return {"ok": False, "error": "Bu başlıkta açık bir pencere yok (uygulama tepsiye küçülmüş olabilir)."}
    core.log(f"Pencere öne getirildi: {t}")
    return {"ok": True, "window": t}


def t_spotify_search(core, a):
    import urllib.parse as up
    q = (a.get("query") or "").strip()
    if not q:
        return {"ok": False, "error": "Arama boş."}
    try:
        os.startfile("spotify:search:" + up.quote(q))
        core.log(f"Spotify uygulamasında arandı: {q}")
        return {"ok": True, "opened": "Spotify uygulaması (arama sonuçları)",
                "note": "Arama sonuçlarını açtım; şarkıyı otomatik başlatamıyorum, kullanıcı tıklamalı."}
    except OSError:
        os.startfile("https://open.spotify.com/search/" + up.quote(q))
        return {"ok": True, "opened": "tarayıcı",
                "note": "Spotify uygulaması bulunamadı, web'de açtım."}


TR_DAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def t_datetime(core, a):
    n = datetime.datetime.now()
    return {"ok": True, "tarih": n.strftime("%d.%m.%Y"), "saat": n.strftime("%H:%M"), "gun": TR_DAYS[n.weekday()]}


def t_end(core, a):
    if core.cfg.get("listen_mode") != "continuous":
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


def t_save_memory(core, a):
    if not core.cfg.get("use_memory", True):
        return {"ok": False, "error": "Hafıza ayarlardan kapatılmış."}
    ok = core.mem_set(a.get("key"), a.get("value"))
    return {"ok": ok} if ok else {"ok": False, "error": "Anahtar ve değer gerekli."}


def t_delete_memory(core, a):
    return {"ok": core.mem_delete(a.get("key") or "")}


def t_list_memory(core, a):
    return {"ok": True, "items": [{"key": m["key"], "value": m["value"]} for m in core.memory[-50:]]}


def t_add_reminder(core, a):
    text = (a.get("text") or "Hatırlatma").strip()[:200]
    due = None
    try:
        if a.get("minutes") not in (None, ""):
            due = time.time() + float(a["minutes"]) * 60
    except Exception:
        due = None
    at = str(a.get("at") or "").strip()
    if due is None and re.fullmatch(r"\d{1,2}:\d{2}", at):
        h, m = (int(x) for x in at.split(":"))
        now = datetime.datetime.now()
        t = now.replace(hour=min(h, 23), minute=min(m, 59), second=0, microsecond=0)
        if t <= now:
            t += datetime.timedelta(days=1)
        due = t.timestamp()
    if due is None:
        return {"ok": False, "error": "Süre (minutes) ya da saat (at, SS:DD) gerekli."}
    core.reminders.append({"id": uuid.uuid4().hex[:6], "text": text, "due": due, "done": False})
    core.save_reminders()
    core.log(f"Hatırlatıcı kuruldu: {text} @ {datetime.datetime.fromtimestamp(due):%H:%M}")
    return {"ok": True, "saat": datetime.datetime.fromtimestamp(due).strftime("%H:%M")}


def t_list_reminders(core, a):
    items = [{"metin": r["text"], "saat": datetime.datetime.fromtimestamp(r["due"]).strftime("%d.%m %H:%M")}
             for r in core.reminders if not r.get("done")]
    return {"ok": True, "hatirlaticilar": items}


def t_delete_reminder(core, a):
    q = norm(a.get("text") or "")
    before = len(core.reminders)
    core.reminders = [r for r in core.reminders if r.get("done") or not (q and q in norm(r["text"]))]
    core.save_reminders()
    return {"ok": len(core.reminders) != before}


def t_system_info(core, a):
    import psutil
    out = {"ok": True, "cpu_yuzde": psutil.cpu_percent(interval=0.5)}
    vm = psutil.virtual_memory()
    out.update(ram_yuzde=vm.percent, ram_kullanilan_gb=round(vm.used / 2 ** 30, 1), ram_toplam_gb=round(vm.total / 2 ** 30, 1))
    try:
        du = psutil.disk_usage((os.environ.get("SystemDrive") or "C:") + "\\")
        out.update(disk_bos_gb=round(du.free / 2 ** 30, 1), disk_dolu_yuzde=du.percent)
    except Exception:
        pass
    try:
        b = psutil.sensors_battery()
        if b:
            out.update(pil_yuzde=round(b.percent), sarjda=bool(b.power_plugged))
    except Exception:
        pass
    out["acik_kalma_saat"] = round((time.time() - psutil.boot_time()) / 3600, 1)
    top = []
    for p in psutil.process_iter(["name", "memory_info"]):
        mi = p.info.get("memory_info")
        if mi:
            top.append((mi.rss, p.info.get("name") or "?"))
    top.sort(reverse=True)
    out["en_cok_ram_kullanan"] = [f"{n} ({round(m / 2 ** 20)} MB)" for m, n in top[:3]]
    return out


def t_weather(core, a):
    import urllib.request
    import urllib.parse as up
    loc = (a.get("location") or core.cfg.get("weather_location") or "").strip()
    req = urllib.request.Request("https://wttr.in/" + up.quote(loc) + "?format=j1&lang=tr",
                                 headers={"User-Agent": "HeyTank/2.2"})
    with urllib.request.urlopen(req, timeout=8) as r:
        d = json.loads(r.read().decode("utf-8"))
    cur = d["current_condition"][0]
    desc = ((cur.get("lang_tr") or cur.get("weatherDesc") or [{"value": ""}])[0]).get("value", "")
    area = ((d.get("nearest_area") or [{}])[0].get("areaName") or [{"value": loc}])[0].get("value", loc)
    return {"ok": True, "yer": area, "durum": desc, "sicaklik_c": cur.get("temp_C"),
            "hissedilen_c": cur.get("FeelsLikeC"), "nem_yuzde": cur.get("humidity"), "ruzgar_kmh": cur.get("windspeedKmph"),
            "gunler": [{"tarih": x.get("date"), "en_yuksek_c": x.get("maxtempC"), "en_dusuk_c": x.get("mintempC")}
                       for x in d.get("weather", [])[:3]]}


def _youtube_first(query):
    import urllib.request
    import urllib.parse as up
    url = "https://www.youtube.com/results?search_query=" + up.quote_plus(query) + "&sp=EgIQAQ%3D%3D"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                                             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                                               "Accept-Language": "tr-TR,tr;q=0.9"})
    html = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")
    m = re.search(r'"videoId":"([\w-]{11})"', html)
    return m.group(1) if m else None


def t_play_media(core, a):
    import urllib.parse as up
    q = (a.get("query") or "").strip()
    if not q:
        return {"ok": False, "error": "Arama boş."}
    if (a.get("provider") or "youtube").lower() == "spotify":
        return t_spotify_search(core, {"query": q})
    try:
        vid = _youtube_first(q)
    except Exception:
        vid = None
    if vid:
        os.startfile("https://www.youtube.com/watch?v=" + vid)
        core.log(f"YouTube'da oynatılıyor: {q}")
        return {"ok": True, "opened": "YouTube", "note": "İlk sonucu doğrudan açtım, otomatik başlar."}
    os.startfile("https://www.youtube.com/results?search_query=" + up.quote_plus(q))
    return {"ok": True, "opened": "YouTube arama sayfası", "note": "İlk videoyu bulamadım, sonuçları açtım."}


def vision_models(client):
    found = []
    try:
        for m in client.models.list():
            n = (m.name or "").replace("models/", "")
            if "generateContent" not in (getattr(m, "supported_actions", None) or []):
                continue
            mt = re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash(-lite)?", n)
            if mt:
                found.append((-float(mt.group(1)), 1 if mt.group(2) else 0, n))
    except Exception:
        pass
    found.sort()
    return list(dict.fromkeys([n for _, _, n in found] + ["gemini-3.8-flash", "gemini-3.5-flash-lite"]))


def t_analyze_screen(core, a):
    from PIL import ImageGrab
    from google.genai import types
    img = ImageGrab.grab()
    img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=75)
    client = make_client(core.get_key())
    q = (a.get("question") or "Ekranda ne var? Kısaca özetle.") + " Türkçe ve kısa cevap ver."
    last = None
    for m in vision_models(client)[:4]:
        try:
            r = client.models.generate_content(
                model=m, contents=[types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"), q])
            return {"ok": True, "answer": (r.text or "")[:1500], "model": m}
        except Exception as e:
            last = e
    return {"ok": False, "error": _short(last, 200)}


APPVOL_PS = r"""
$ErrorActionPreference = "Stop"
Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
class MMDeviceEnumeratorComObject { }

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    int EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
}

[Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionManager2 {
    int GetAudioSessionControl(IntPtr a, int b, out IntPtr c);
    int GetSimpleAudioVolume(IntPtr a, int b, out IntPtr c);
    int GetSessionEnumerator(out IAudioSessionEnumerator e);
}

[Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionEnumerator {
    int GetCount(out int count);
    int GetSession(int index, out IAudioSessionControl2 session);
}

[Guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionControl2 {
    int GetState(out int s);
    int GetDisplayName([MarshalAs(UnmanagedType.LPWStr)] out string n);
    int SetDisplayName([MarshalAs(UnmanagedType.LPWStr)] string n, ref Guid c);
    int GetIconPath([MarshalAs(UnmanagedType.LPWStr)] out string p);
    int SetIconPath([MarshalAs(UnmanagedType.LPWStr)] string p, ref Guid c);
    int GetGroupingParam(out Guid g);
    int SetGroupingParam(ref Guid g, ref Guid c);
    int RegisterAudioSessionNotification(IntPtr n);
    int UnregisterAudioSessionNotification(IntPtr n);
    int GetSessionIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string id);
    int GetSessionInstanceIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string id);
    int GetProcessId(out uint pid);
    int IsSystemSoundsSession();
    int SetDuckingPreference(bool optOut);
}

[Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ISimpleAudioVolume {
    int SetMasterVolume(float level, ref Guid ctx);
    int GetMasterVolume(out float level);
    int SetMute(bool mute, ref Guid ctx);
    int GetMute(out bool mute);
}

public static class AppVol {
    public static string Run(string action, string app, int level) {
        IMMDeviceEnumerator en = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
        IMMDevice dev;
        Marshal.ThrowExceptionForHR(en.GetDefaultAudioEndpoint(0, 1, out dev));
        Guid iid = new Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F");
        object o;
        Marshal.ThrowExceptionForHR(dev.Activate(ref iid, 23, IntPtr.Zero, out o));
        IAudioSessionManager2 mgr = (IAudioSessionManager2)o;
        IAudioSessionEnumerator se;
        Marshal.ThrowExceptionForHR(mgr.GetSessionEnumerator(out se));
        int n;
        se.GetCount(out n);
        List<string> all = new List<string>();
        List<string> hit = new List<string>();
        string needle = (app == null ? "" : app).ToLowerInvariant();
        for (int i = 0; i < n; i++) {
            IAudioSessionControl2 c;
            se.GetSession(i, out c);
            uint pid;
            c.GetProcessId(out pid);
            string name = "sistem";
            if (pid != 0) {
                try { name = Process.GetProcessById((int)pid).ProcessName; } catch (Exception) { }
            }
            ISimpleAudioVolume v = (ISimpleAudioVolume)c;
            float vol;
            bool mute;
            v.GetMasterVolume(out vol);
            v.GetMute(out mute);
            all.Add(name + "=" + Math.Round(vol * 100) + (mute ? "(sessiz)" : ""));
            if (action != "list" && needle.Length > 0 && name.ToLowerInvariant().Contains(needle)) {
                Guid ctx = Guid.Empty;
                float nv = vol;
                if (action == "set") nv = level / 100f;
                else if (action == "up") nv = Math.Min(1f, vol + level / 100f);
                else if (action == "down") nv = Math.Max(0f, vol - level / 100f);
                if (action == "mute") v.SetMute(true, ref ctx);
                else if (action == "unmute") v.SetMute(false, ref ctx);
                else v.SetMasterVolume(nv, ref ctx);
                hit.Add(name + "=" + Math.Round(nv * 100));
            }
        }
        if (action == "list") return "LIST|" + string.Join(", ", all.ToArray());
        if (hit.Count == 0) return "NOMATCH|" + string.Join(", ", all.ToArray());
        return "OK|" + string.Join(", ", hit.ToArray());
    }
}
"@
Write-Output ([AppVol]::Run($Action, $App, [int]$Level))
"""


def appvol_ps(action, app="", level=0):
    head = "$Action = '%s'\n$App = '%s'\n$Level = %d\n" % (action, str(app).replace("'", "''"), int(level))
    enc = base64.b64encode((head + APPVOL_PS).encode("utf-16-le")).decode()
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-EncodedCommand", enc], capture_output=True, text=True, timeout=30,
                           creationflags=0x08000000)
        out = (r.stdout or "").strip()
        return r.returncode == 0 and "|" in out, (out or (r.stderr or "")[:200])
    except Exception as e:
        return False, repr(e)


def t_app_volume(core, a):
    act = a.get("action") or "set"
    if act not in ("set", "up", "down", "mute", "unmute"):
        return {"ok": False, "error": "Bilinmeyen eylem."}
    try:
        lvl = max(0, min(100, int(a.get("amount") if a.get("amount") not in (None, "") else 10)))
    except Exception:
        lvl = 10
    ok, out = appvol_ps(act, a.get("app") or "", lvl)
    if not ok:
        return {"ok": False, "error": "Ses karıştırıcıya erişilemedi: " + out[:150]}
    kind, _, rest = out.partition("|")
    if kind == "OK":
        core.log(f"Uygulama sesi: {rest}")
        return {"ok": True, "uygulamalar": rest}
    return {"ok": False, "error": "Bu ada sahip ses çalan uygulama yok.", "calan_uygulamalar": rest,
            "note": "Ses çalan uygulamalar: " + (rest or "yok") + ". Doğru adı seçip bir kez daha dene."}


def t_list_audio_apps(core, a):
    ok, out = appvol_ps("list")
    if not ok:
        return {"ok": False, "error": "Ses karıştırıcıya erişilemedi: " + out[:150]}
    return {"ok": True, "uygulamalar": out.partition("|")[2]}


FOLDER_NAMES = {"masaüstü": ["Desktop", "Masaüstü"], "desktop": ["Desktop", "Masaüstü"],
                "belgeler": ["Documents", "Belgeler"], "documents": ["Documents", "Belgeler"],
                "indirilenler": ["Downloads", "İndirilenler"], "downloads": ["Downloads", "İndirilenler"],
                "müzik": ["Music", "Müzik"], "music": ["Music", "Müzik"],
                "videolar": ["Videos", "Videolar"], "videos": ["Videos", "Videolar"],
                "resimler": ["Pictures", "Resimler"], "pictures": ["Pictures", "Resimler"]}


def _known_dir(alias):
    home = Path(os.path.expanduser("~"))
    for nm in FOLDER_NAMES.get(norm(alias).replace(" ", ""), FOLDER_NAMES.get((alias or "").strip().lower(), [])) or []:
        for base in (home, home / "OneDrive"):
            if (base / nm).is_dir():
                return base / nm
    return None


def _default_roots():
    out = []
    for k in ("desktop", "documents", "downloads", "music", "videos", "pictures"):
        d = _known_dir(k)
        if d and d not in out:
            out.append(d)
    return out


def _resolve_path(text):
    t = os.path.expandvars(os.path.expanduser((text or "").strip().strip('"')))
    if not t:
        return None
    parts = re.split(r"[\\/]+", t)
    d = _known_dir(parts[0]) if not re.match(r"^[A-Za-z]:$", parts[0]) else None
    p = Path(d, *parts[1:]) if d else Path(t)
    return p if p.exists() else None


def _search_files(query, roots, kind="any", limit=8, max_scan=60000, tmax=7.0):
    toks = [x for x in norm(query).split() if x]
    t0, scanned, hits, capped = time.time(), 0, [], False
    skip = {"node_modules", "appdata", "$recycle.bin", "windows", "system volume information", ".git"}
    for root in roots:
        base_depth = len(Path(root).parts)
        for dp, dn, fn in os.walk(root):
            if len(Path(dp).parts) - base_depth >= 4:
                dn[:] = []
            dn[:] = [d for d in dn if d.lower() not in skip and not d.startswith(".")]
            entries = ([(d, "klasör") for d in dn] if kind != "file" else []) + ([(f, "dosya") for f in fn] if kind != "dir" else [])
            for name, typ in entries:
                scanned += 1
                nn = norm(name)
                if toks and all(x in nn for x in toks):
                    hits.append({"path": str(Path(dp) / name), "tur": typ, "_s": (len(Path(dp).parts), len(name))})
            if scanned > max_scan or time.time() - t0 > tmax:
                capped = True
                break
        if capped or len(hits) >= limit * 4:
            break
    hits.sort(key=lambda h: h["_s"])
    for h in hits:
        h.pop("_s", None)
    return hits[:limit], capped


def t_find_files(core, a):
    q = (a.get("query") or "").strip()
    if not q:
        return {"ok": False, "error": "Aranacak ad boş."}
    roots = _default_roots()
    folder = (a.get("folder") or "").strip()
    if folder:
        p = _resolve_path(folder)
        if p and p.is_dir():
            roots = [p]
        else:
            found, _ = _search_files(folder, roots, kind="dir", limit=3)
            roots = [Path(f["path"]) for f in found] or roots
    if not folder:
        for L in "DEFG":
            d = Path(L + ":\\")
            if d.exists() and d not in roots:
                roots.append(d)
    res, capped = _search_files(q, roots, kind=a.get("kind") or "any")
    out = {"ok": True, "sonuclar": res}
    if not res:
        out["status"] = "no_results"
        out["note"] = "Eşleşen bulunamadı" + (" (tarama sınırına ulaşıldı, klasör belirtmeyi dene)." if capped else ".")
    elif capped:
        out["note"] = "Tarama sınırına ulaşıldı; liste eksik olabilir."
    return out


def t_list_folder(core, a):
    p = _resolve_path(a.get("path"))
    if not p or not p.is_dir():
        return {"ok": False, "error": "Klasör bulunamadı."}
    items = []
    try:
        for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:40]:
            items.append(("[klasör] " if e.is_dir() else "") + e.name)
    except Exception as e:
        return {"ok": False, "error": _short(e)}
    return {"ok": True, "klasor": str(p), "icerik": items}


RISKY_EXT = {".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".msi", ".reg", ".scr", ".hta", ".jar"}


def t_open_path(core, a):
    p = _resolve_path(a.get("path"))
    if not p:
        return {"ok": False, "error": "Yol bulunamadı. Önce find_files ile ara."}
    if p.suffix.lower() in RISKY_EXT and not core.confirm("Dosyayı çalıştır", str(p)):
        return {"ok": False, "error": "Kullanıcı onaylamadı."}
    if a.get("args"):
        os.startfile(str(p), arguments=str(a["args"]))
    else:
        os.startfile(str(p))
    core.log(f"Açıldı: {p}")
    return {"ok": True, "opened": str(p)}


def _steam_libs():
    paths = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            paths.append(winreg.QueryValueEx(k, "SteamPath")[0])
    except Exception:
        pass
    for pf in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if pf:
            paths.append(os.path.join(pf, "Steam"))
    libs = []
    for sp in paths:
        sa = Path(sp) / "steamapps"
        if sa.is_dir() and sa not in libs:
            libs.append(sa)
        vdf = sa / "libraryfolders.vdf"
        if vdf.exists():
            txt = vdf.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
                lib = Path(m.group(1).replace("\\\\", "\\")) / "steamapps"
                if lib.is_dir() and lib not in libs:
                    libs.append(lib)
    return libs


def scan_games():
    games = []
    for lib in _steam_libs():
        for f in lib.glob("appmanifest_*.acf"):
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore")
                aid = re.search(r'"appid"\s+"(\d+)"', txt)
                nm = re.search(r'"name"\s+"([^"]+)"', txt)
                if aid and nm and not re.search(r"redistributable|proton|steam linux runtime|steamworks", nm.group(1), re.I):
                    games.append({"name": nm.group(1), "kind": "Steam", "launch": f"steam://rungameid/{aid.group(1)}"})
            except Exception:
                pass
    epic = Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    if epic.is_dir():
        for f in epic.glob("*.item"):
            try:
                d = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
                exe = Path(d.get("InstallLocation", "")) / d.get("LaunchExecutable", "")
                if d.get("DisplayName") and exe.is_file():
                    games.append({"name": d["DisplayName"], "kind": "Epic", "launch": str(exe)})
            except Exception:
                pass
    return games


def t_launch_game(core, a):
    name = (a.get("name") or "").strip()
    n = norm(name)
    if not n:
        return {"ok": False, "error": "Oyun adı boş."}
    games = scan_games()
    names = [norm(g["name"]) for g in games]
    pick = None
    cont = [i for i, x in enumerate(names) if n in x or x in n]
    if cont:
        pick = min(cont, key=lambda i: len(names[i]))
    else:
        m = difflib.get_close_matches(n, names, n=1, cutoff=0.6)
        if m:
            pick = names.index(m[0])
    if pick is not None:
        g = games[pick]
        os.startfile(g["launch"])
        core.log(f"Oyun başlatıldı: {g['name']} ({g['kind']})")
        return {"ok": True, "opened": g["name"], "kaynak": g["kind"]}
    r = t_open_app(core, {"name": name})
    if r.get("ok"):
        return r
    return {"ok": False, "error": f"'{name}' oyunu Steam/Epic kütüphanesinde ve Başlat menüsünde bulunamadı.",
            "kutuphanede": [g["name"] for g in games[:15]],
            "note": "Oyun başka bir yerdeyse find_files ile exe'sini bulup open_path ile aç."}


TOOL_GROUPS = {"open_url": "Web & Uygulama", "web_search": "Web & Uygulama", "open_app": "Web & Uygulama",
               "run_shortcut": "Web & Uygulama", "focus_window": "Web & Uygulama", "press_keys": "Web & Uygulama",
               "launch_game": "Dosya & Oyun", "find_files": "Dosya & Oyun", "list_folder": "Dosya & Oyun",
               "open_path": "Dosya & Oyun", "media_control": "Medya & Ses", "play_media": "Medya & Ses",
               "spotify_search": "Medya & Ses", "set_volume": "Medya & Ses", "set_app_volume": "Medya & Ses",
               "list_audio_apps": "Medya & Ses", "system_info": "Sistem & Bilgi",
               "get_weather": "Sistem & Bilgi", "analyze_screen": "Sistem & Bilgi", "get_datetime": "Sistem & Bilgi",
               "lock_computer": "Sistem & Bilgi", "take_screenshot": "Sistem & Bilgi", "show_desktop": "Sistem & Bilgi",
               "save_memory": "Hafıza & Hatırlatıcı", "delete_memory": "Hafıza & Hatırlatıcı",
               "list_memory": "Hafıza & Hatırlatıcı", "add_reminder": "Hafıza & Hatırlatıcı",
               "list_reminders": "Hafıza & Hatırlatıcı", "delete_reminder": "Hafıza & Hatırlatıcı",
               "end_conversation": "Oturum", "close_app": "Gelişmiş", "run_powershell": "Gelişmiş"}


TOOL_DEFS = [
    dict(name="open_url", label="Web sitesi aç", danger=False, fn=t_open_url,
         desc="Verilen web adresini tarayıcıda açar (youtube.com, spotify, steam:// vb.).",
         params={"url": _p("STRING", "Açılacak adres, örn. https://www.youtube.com")}, req=["url"]),
    dict(name="web_search", label="Arama yap", danger=False, fn=t_web_search,
         desc="Tarayıcıda Google veya YouTube araması yapar (Spotify için spotify_search kullan).",
         params={"query": _p("STRING", "Aranacak ifade"),
                 "site": _p("STRING", "Nerede aranacak", ["google", "youtube"])}, req=["query"]),
    dict(name="open_app", label="Uygulama aç", danger=False, fn=t_open_app,
         desc="Bilgisayardaki bir uygulamayı adıyla açar (Spotify, Discord, Not Defteri, Chrome...).",
         params={"name": _p("STRING", "Uygulamanın adı")}, req=["name"]),
    dict(name="set_app_volume", label="Uygulama sesi", danger=False, fn=t_app_volume,
         desc="Tek bir uygulamanın sesini ayarlar (Spotify, Discord, Chrome, oyunlar...): belirli yüzdeye getir, artır, azalt, sessize al.",
         params={"app": _p("STRING", "Uygulamanın adı, örn. spotify, discord, chrome"),
                 "action": _p("STRING", "İşlem", ["set", "up", "down", "mute", "unmute"]),
                 "amount": _p("INTEGER", "Yüzde (set için hedef seviye, up/down için değişim)")}, req=["app", "action"]),
    dict(name="list_audio_apps", label="Ses çalan uygulamalar", danger=False, fn=t_list_audio_apps,
         desc="Şu an ses çalan uygulamaları ve ses seviyelerini listeler.", params={}, req=[]),
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
    dict(name="spotify_search", label="Spotify'da ara", danger=False, fn=t_spotify_search,
         desc="Spotify masaüstü uygulamasında şarkı/sanatçı/albüm arar (tarayıcıda değil). Sonuçları açar, otomatik çalmaz.",
         params={"query": _p("STRING", "Aranacak şarkı, sanatçı veya albüm")}, req=["query"]),
    dict(name="press_keys", label="Klavye kısayolu gönder", danger=False, fn=t_press_keys,
         desc="Klavye kısayolu gönderir (ctrl+shift+m, ctrl+t, win+d, f5...). Metin yazmaz. Uygulama içi işler için (Discord mikrofon tuşu, tarayıcı sekmesi vb.); odaktaki pencereye gider.",
         params={"keys": _p("STRING", "Artı ile ayrılmış tuşlar, örn. ctrl+shift+m")}, req=["keys"]),
    dict(name="focus_window", label="Pencereyi öne getir", danger=False, fn=t_focus_window,
         desc="Başlığında verilen metin geçen açık pencereyi öne getirir (Discord, Spotify, Chrome...). Tuş göndermeden önce kullan.",
         params={"title": _p("STRING", "Pencere başlığında geçen metin")}, req=["title"]),
    dict(name="get_datetime", label="Tarih ve saat", danger=False, fn=t_datetime,
         desc="Şu anki tarih, saat ve günü verir.", params={}, req=[]),
    dict(name="add_reminder", label="Hatırlatıcı kur", danger=False, fn=t_add_reminder,
         desc="Belirli bir süre sonra (minutes) ya da belirli saatte (at, 'SS:DD') sesli ve bildirimli hatırlatma kurar. Kalıcıdır, uygulama kapansa da korunur.",
         params={"text": _p("STRING", "Hatırlatılacak şey"), "minutes": _p("NUMBER", "Kaç dakika sonra"),
                 "at": _p("STRING", "Saat, örn. 18:30")}, req=["text"]),
    dict(name="list_reminders", label="Hatırlatıcıları listele", danger=False, fn=t_list_reminders,
         desc="Bekleyen hatırlatıcıları listeler.", params={}, req=[]),
    dict(name="delete_reminder", label="Hatırlatıcı sil", danger=False, fn=t_delete_reminder,
         desc="Metninde verilen ifade geçen hatırlatıcıyı siler.",
         params={"text": _p("STRING", "Silinecek hatırlatıcıyı tarif eden ifade")}, req=["text"]),
    dict(name="save_memory", label="Hafızaya kaydet", danger=False, fn=t_save_memory,
         desc="Kullanıcı hakkında kalıcı bilgiyi kaydeder (adı, tercihleri, projeleri). Sessizce çağır.",
         params={"key": _p("STRING", "Kısa başlık, örn. 'favori müzik'"), "value": _p("STRING", "Bilgi")}, req=["key", "value"]),
    dict(name="delete_memory", label="Hafızadan sil", danger=False, fn=t_delete_memory,
         desc="Hafızadaki bir kaydı anahtarıyla siler.", params={"key": _p("STRING", "Silinecek kaydın başlığı")}, req=["key"]),
    dict(name="list_memory", label="Hafızayı listele", danger=False, fn=t_list_memory,
         desc="Hafızadaki kayıtları listeler.", params={}, req=[]),
    dict(name="system_info", label="Sistem bilgisi", danger=False, fn=t_system_info,
         desc="CPU, RAM, disk, pil durumu, açık kalma süresi ve en çok bellek kullanan programları verir.", params={}, req=[]),
    dict(name="get_weather", label="Hava durumu", danger=False, fn=t_weather,
         desc="Anlık hava durumu ve 3 günlük tahmin (wttr.in). Konum verilmezse ayarlardaki ya da IP'den bulunan konum kullanılır.",
         params={"location": _p("STRING", "Şehir adı (isteğe bağlı)")}, req=[]),
    dict(name="play_media", label="Müzik / video oynat", danger=False, fn=t_play_media,
         desc="YouTube'da aranan şeyin ilk sonucunu doğrudan açıp oynatır. Spotify için provider='spotify' (yalnızca arama sonuçlarını açar).",
         params={"query": _p("STRING", "Şarkı, sanatçı veya video adı"),
                 "provider": _p("STRING", "youtube veya spotify", ["youtube", "spotify"])}, req=["query"]),
    dict(name="analyze_screen", label="Ekranı analiz et", danger=False, fn=t_analyze_screen,
         desc="Ekran görüntüsü alıp Gemini ile inceler ('ekranda ne var', 'bu hatayı oku'). Görüntü yalnızca bu araç çağrılınca Google'a gider.",
         params={"question": _p("STRING", "Ekran hakkında sorulacak soru")}, req=[]),
    dict(name="launch_game", label="Oyun başlat", danger=False, fn=t_launch_game,
         desc="Adıyla oyun başlatır: Steam ve Epic kütüphanelerini ve Başlat menüsünü tarar.",
         params={"name": _p("STRING", "Oyunun adı")}, req=["name"]),
    dict(name="find_files", label="Dosya / klasör ara", danger=False, fn=t_find_files,
         desc="Belgeler, Masaüstü, İndirilenler vb. ve sürücülerde dosya/klasör adıyla arar. folder verilirse yalnızca orada arar.",
         params={"query": _p("STRING", "Dosya/klasör adında geçen kelimeler"),
                 "folder": _p("STRING", "Aranacak klasör (isteğe bağlı): masaüstü, indirilenler, bir yol veya klasör adı"),
                 "kind": _p("STRING", "Ne aranıyor", ["any", "file", "dir"])}, req=["query"]),
    dict(name="list_folder", label="Klasör içeriği", danger=False, fn=t_list_folder,
         desc="Bir klasörün içindekileri listeler (masaüstü, indirilenler veya tam yol).",
         params={"path": _p("STRING", "Klasör yolu ya da takma adı")}, req=["path"]),
    dict(name="open_path", label="Dosya / program aç", danger=False, fn=t_open_path,
         desc="Bir dosyayı, klasörü ya da programı (exe) açar/çalıştırır; isteğe bağlı parametre verilebilir. Betik/yükleyici dosyalar için onay ister.",
         params={"path": _p("STRING", "Tam yol veya find_files'tan gelen yol"), "args": _p("STRING", "Program parametreleri (isteğe bağlı)")}, req=["path"]),
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


def hint_for_error(err):
    e = str(err or "")
    if re.search(r"API key not valid|API_KEY_INVALID|invalid api key", e, re.I):
        return "Anahtar geçersiz görünüyor."
    if re.search(r"location is not supported|FAILED_PRECONDITION", e, re.I):
        return "Google bu bölgede servisi desteklemiyor olabilir."
    if re.search(r"429|quota|RESOURCE_EXHAUSTED", e, re.I):
        return "Kota dolmuş görünüyor."
    if re.search(r"403|PERMISSION_DENIED", e, re.I):
        return "İzin reddedildi."
    return _short(e, 160)


def finish_result(res):
    ok = bool(res.get("ok"))
    res.setdefault("status", "ok" if ok else "error")
    res.setdefault("retryable", False)
    res.setdefault("message", res.get("note") or res.get("error") or ("Tamamlandı." if ok else "İşlem başarısız oldu."))
    return res


def run_tool(core, name, args):
    t = TOOLS.get(name)
    if not t or not core.tool_enabled(name):
        core.log(f"Araç reddedildi: {name}")
        return finish_result({"ok": False, "error": "Bu araç kapalı veya yok."})
    try:
        if t["danger"]:
            detail = args.get("script") or args.get("name") or json.dumps(args, ensure_ascii=False)
            if not core.confirm(t["label"], str(detail)):
                core.log(f"Onaylanmadı: {name}")
                return finish_result({"ok": False, "error": "Kullanıcı onaylamadı."})
        res = finish_result(t["fn"](core, args))
        core.log(f"Araç: {name} {json.dumps(args, ensure_ascii=False)[:120]}")
        core.fb("ok" if res.get("ok") else "fail")
        return res
    except Exception as e:
        core.log(f"Araç hatası ({name}): {_short(e)}")
        core.fb("fail")
        return finish_result({"ok": False, "error": _short(e)})


LANGS = {"tr": ("tr-TR", "Türkçe", "Turkish"), "en": ("en-US", "English", "English"),
         "de": ("de-DE", "Deutsch", "German"), "fr": ("fr-FR", "Français", "French"),
         "es": ("es-ES", "Español", "Spanish")}


def build_vocab(core):
    words = ["Hey Tank", "Tank", "Spotify", "Discord", "Steam", "YouTube", "WhatsApp"]
    words += [x["name"] for x in core.shortcuts if x.get("enabled", True)]
    words += [g["name"] for g in core.games_cache[:25]]
    out = []
    for w in words:
        w = str(w).strip()[:40]
        if w and w not in out:
            out.append(w)
    return out[:60]


def system_prompt(core, manual=False):
    cfg = core.cfg
    mode = cfg.get("listen_mode", "wake")
    sc = "; ".join(x["name"] + (f" ({x['hint']})" if x.get("hint") else "")
                   for x in core.shortcuts if x.get("enabled", True)) or "yok"
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    lang = cfg.get("language", "tr")
    if lang in LANGS:
        ln, en = LANGS[lang][1], LANGS[lang][2]
        lang_rule = (f"Kullanıcı yalnızca {ln} konuşur. Duyduğun sesi her zaman {ln} olarak yorumla (başka bir dil gibi "
                     f"görünse bile) ve yalnızca {ln} cevap ver. RESPOND IN {en.upper()}. YOU MUST RESPOND UNMISTAKABLY "
                     f"IN {en.upper()}. ")
    else:
        lang_rule = ("Kullanıcı büyük olasılıkla Türkçe konuşur; ses net değilse Türkçe varsay. Kullanıcı hangi dilde "
                     "konuşursa o dilde cevap ver, belirsizse Türkçe. ")
    if mode == "continuous":
        gate = ("Sürekli dinleme modundasın: çevredeki konuşmaları (TV, başkalarıyla sohbet) ve sana yönelik olmayan "
                "sesleri duymazdan gel ve sessiz kal. Yalnızca adın ('Tank' ya da 'Hey Tank') geçtiğinde veya sana açıkça "
                "bir istek, soru ya da sohbet yöneltildiğinde cevap ver. end_conversation aracını çağırma. ")
    elif manual:
        gate = "Oturum kullanıcı tarafından elle başlatıldı; 'Hey Tank' beklemeden dinle. "
    else:
        gate = ("Oturum 'Hey Tank' uyandırma kelimesiyle otomatik başladı ve yanlış uyanma olabilir. Konuşmanın başında "
                "'Hey Tank' ya da sana yönelik herhangi bir söz (istek, soru, sohbet) duymazsan, yani başkasıyla "
                "konuşma, TV, gürültü veya sessizlik varsa HİÇBİR ŞEY SÖYLEME ve hemen end_conversation aracını çağır. ")
    mem = mem_rule = ""
    if cfg.get("use_memory", True):
        if core.memory:
            items = "; ".join(f"{m['key']}: {m['value']}" for m in core.memory[-40:])
            mem = f"Kullanıcı hakkında hatırladıkların: {items[:1800]}. "
        mem_rule = ("Kullanıcı kalıcı bir bilgi söylerse (adı, tercihleri, projeleri, alışkanlıkları) save_memory ile "
                    "sessizce kaydet; bilgi yanlış ya da eskiyse delete_memory ile sil. ")
    custom = (cfg.get("custom_instructions") or "").strip()
    custom = f"Kullanıcının ek talimatları: {custom[:1200]}. " if custom else ""
    return ("Sen Tank'sın: kullanıcının Windows bilgisayarını yöneten ve aynı zamanda onunla sohbet edebilen zeki, "
            "samimi, hafif esprili bir sesli asistansın. Kullanıcıya 'sen' diye hitap et. " + lang_rule +
            "Sohbet, soru, fikir ve açıklama isteklerinde normal bir arkadaş gibi doğal konuş; yeterince açıklayıcı ol "
            "ama gereksiz uzatma. Sesli konuştuğunu unutma: madde işareti, markdown ve emoji kullanma. "
            "Bilgisayarla ilgili isteklerde araçları kullan: önce kısa bir onay ver (örn. 'Açıyorum.'), iş bitince "
            "sonucu bildir. Aracı çağırmadan 'yaptım' deme, aracın sonucundaki 'message' alanına göre dürüstçe bildir. "
            "Bir araç sonuç vermez ya da hata dönerse aynı aracı parametreleri değiştirip tekrar tekrar deneme; durumu "
            "kullanıcıya söyle. Konuşmadan art arda ikiden fazla araç çağırma. "
            + gate +
            "Asla 'yetkim yok' deme. Doğrudan aracın yoksa önce araçları birleştirmeyi dene: uygulama içi işlerde "
            "focus_window + press_keys ile uygulamanın bilinen klavye kısayolunu kullan (Discord mikrofon gibi işlerde "
            "kayıtlı bir kısayol varsa onu çalıştır). Yine olmuyorsa ne yapamadığını ve kullanıcının ne yapabileceğini "
            "(örn. Discord'da Tuş Atamaları'ndan mikrofon tuşu atamak) tek cümleyle söyle. "
            "Silme, kapatma gibi riskli işlerde önce sözlü onay iste. Anlamazsan kısaca tekrar iste. "
            + ("Kullanıcı sohbeti bitirince (teşekkürler, tamam, kapat gibi) end_conversation aracını çağır. " if mode != "continuous" else "")
            + mem_rule + mem + custom +
            f"Kayıtlı kısayollar: {sc}. Şu an: {now}.")


LIVE_FALLBACK_MODELS = ["gemini-3.8-live", "gemini-3.8-live-extended-thinking", "gemini-2.5-flash-native-audio-latest"]

LIVE_LABELS = {"gemini-3.8-live": "Gemini 3.8 Live (önerilen)",
               "gemini-3.8-live-extended-thinking": "Gemini 3.8 Live · Derin düşünme (daha yavaş)"}


def rank_live_models(names):
    groups = {}
    for n in names:
        low = n.lower()
        if n in DEAD_MODELS or any(x in low for x in ("translate", "tts", "transcribe")):
            continue
        if low == "gemini-3.8-live":
            r = 0
        elif low.startswith("gemini-3.8-live"):
            r = 1
        elif "live" in low and "gemini-3" in low:
            r = 2
        elif "native-audio" in low:
            r = 3
        elif "live" in low:
            r = 4
        else:
            r = 5
        groups.setdefault(r, []).append(n)
    out = []
    for r in sorted(groups):
        out += sorted(groups[r], reverse=True)
    return out


def list_live_models(key):
    client = make_client(key)
    live, allm = [], []
    for m in client.models.list():
        n = (m.name or "").replace("models/", "")
        allm.append(n)
        if "bidiGenerateContent" in (getattr(m, "supported_actions", None) or []):
            live.append(n)
    if not live:
        live = [n for n in allm if "live" in n.lower() or "native-audio" in n.lower()]
    return {"live": live, "all": allm}


def make_client(key, version=""):
    from google import genai
    if not version:
        return genai.Client(api_key=key)
    from google.genai import types
    return genai.Client(api_key=key, http_options=types.HttpOptions(api_version=version))


async def probe_models(key, models, version=""):
    client = make_client(key, version)

    async def one(m):
        try:
            async with client.aio.live.connect(model=m, config={"response_modalities": ["AUDIO"]}):
                await asyncio.sleep(0.3)
            return {"model": m, "ok": True, "error": ""}
        except Exception as e:
            return {"model": m, "ok": False, "error": _short(e, 160)}

    return list(await asyncio.gather(*(one(m) for m in models)))


class Core:
    def __init__(self):
        raw = load_json(CONFIG_PATH, {})
        self.cfg = dict(DEFAULT_CONFIG)
        for k in DEFAULT_CONFIG:
            if k in raw:
                self.cfg[k] = raw[k]
        if not isinstance(self.cfg.get("tools"), dict):
            self.cfg["tools"] = {}
        if self.cfg.get("live_model") in DEAD_MODELS:
            self.cfg["live_model"] = ""
            self.cfg["api_version"] = ""
        if raw.get("cfg_ver", 0) < 5:
            if raw.get("wake_mode") == "off":
                self.cfg["listen_mode"] = "tap"
            if raw.get("idle_sec") in (None, 12):
                self.cfg["idle_sec"] = 20
            self.cfg["cfg_ver"] = 5
            save_json(CONFIG_PATH, self.cfg)
        self.shortcuts = load_shortcuts()
        if not self.cfg.get("seeded_v21"):
            if not any(norm(x["name"]) == "discord mikrofon" for x in self.shortcuts):
                self.shortcuts.append({"id": uuid.uuid4().hex[:8], "name": "Discord mikrofon",
                                       "hint": "discord mikrofonu kapat/aç, sustur", "targets": [],
                                       "keys": "ctrl+shift+m", "focus": "", "mode": "all", "args": "",
                                       "enabled": False})
                save_json(SHORTCUTS_PATH, self.shortcuts)
            self.cfg["seeded_v21"] = True
            save_json(CONFIG_PATH, self.cfg)
        self.memory = [m for m in (load_json(MEMORY_PATH, {"items": []}) or {}).get("items", [])
                       if isinstance(m, dict) and m.get("key")]
        rem = load_json(REMINDERS_PATH, [])
        self.reminders = [r for r in rem if isinstance(r, dict) and "due" in r] if isinstance(rem, list) else []
        self.games_cache = []
        self.lock = threading.Lock()
        self.logs = deque(maxlen=400)
        self.seq = 0
        self.chat = []
        self.state = {"state": "loading", "status": "Başlatılıyor...", "level": 0.0, "level_out": 0.0,
                      "heard": "", "confirm": None, "beat": time.time()}
        self._confirm_evt = threading.Event()
        self._confirm_ok = False
        self._confirm_id = None
        self.attention = None
        self.notify = None
        self.engine = Engine(self)
        self.live = Live(self)

    def log(self, msg):
        line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
        with self.lock:
            self.seq += 1
            self.logs.append((self.seq, line))
        if self.cfg.get("log_to_file", True):
            try:
                LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1_000_000:
                    LOG_PATH.replace(LOG_PATH.with_suffix(".old.log"))
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.datetime.now():%Y-%m-%d} {line}\n")
            except Exception:
                pass

    def mem_set(self, key, value):
        key, value = (key or "").strip()[:60], (value or "").strip()[:300]
        if not key or not value:
            return False
        for m in self.memory:
            if norm(m["key"]) == norm(key):
                m["value"], m["t"] = value, time.time()
                break
        else:
            self.memory.append({"key": key, "value": value, "t": time.time()})
        del self.memory[:-200]
        save_json(MEMORY_PATH, {"items": self.memory})
        return True

    def mem_delete(self, key):
        n, before = norm(key or ""), len(self.memory)
        self.memory = [m for m in self.memory if norm(m["key"]) != n]
        if len(self.memory) != before:
            save_json(MEMORY_PATH, {"items": self.memory})
        return len(self.memory) != before

    def save_reminders(self):
        save_json(REMINDERS_PATH, self.reminders)

    def start_background(self):
        threading.Thread(target=self._reminder_loop, daemon=True).start()
        threading.Thread(target=self._warm_caches, daemon=True).start()

    def _warm_caches(self):
        try:
            self.games_cache = scan_games()
        except Exception:
            pass
        try:
            get_apps()
        except Exception:
            pass

    def _reminder_loop(self):
        while not self.engine.stop.is_set():
            now = time.time()
            changed = False
            for r in self.reminders:
                if not r.get("done") and r["due"] <= now:
                    r["done"] = True
                    changed = True
                    if now - r["due"] < 6 * 3600:
                        self.log(f"Hatırlatıcı: {r['text']}")
                        play_sound("bell", self.cfg.get("beep_volume", 60))
                        self.set_chat("model", "⏰ " + r["text"])
                        self.close_chat()
                        if self.notify:
                            self.notify("Hey Tank", r["text"])
            if changed:
                self.reminders = [r for r in self.reminders if not r.get("done")][-100:] or []
                self.save_reminders()
            time.sleep(5)

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
        elif portable:
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
        if self.cfg.get("listen_mode") == "continuous":
            on = not self.cfg.get("listening", True)
            self.cfg["listening"] = on
            self.save_cfg()
            if not on:
                self.live.request_stop()
            return
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


class Engine(threading.Thread):
    def __init__(self, core):
        super().__init__(daemon=True)
        self.core = core
        self.stop = threading.Event()
        self.reopen = threading.Event()
        self.manual = threading.Event()
        self.models = {}
        self.last_use = time.time()
        self.next_try = 0.0
        self.fail = 0
        self.hotkey_ok = None

    def _hotkey_loop(self):
        try:
            import ctypes.wintypes as wt
            u = ctypes.windll.user32
            msg, cur = wt.MSG(), None
            while not self.stop.is_set():
                combo = (self.core.cfg.get("hotkey_combo") or "ctrl+alt+t").lower()
                if combo != cur:
                    if cur is not None:
                        u.UnregisterHotKey(None, 1)
                    cur = combo
                    mods, vk = hotkey_parts(combo)
                    self.hotkey_ok = bool(vk and u.RegisterHotKey(None, 1, mods | 0x4000, vk))
                    if self.hotkey_ok:
                        self.core.log(f"Kısayol kaydedildi: {combo}")
                    else:
                        self.core.log(f"Kısayol ({combo}) kaydedilemedi; geçersiz ya da başka uygulama kullanıyor.")
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

    def start_live(self, aq, rec, noise, preroll=None, manual=False):
        core = self.core
        thr = float(min(2500.0, max(300.0, np.median(noise) * 2.5))) if len(noise) else 300.0
        if rec is not None:
            rec.Reset()
        if core.cfg.get("beep_sound", "rising") != "none" and core.cfg.get("listen_mode") != "continuous":
            threading.Thread(target=play_sound, args=(core.cfg.get("beep_sound", "rising"),
                             core.cfg.get("beep_volume", 60)), daemon=True).start()
        try:
            core.live.run(aq, thr, preroll, manual)
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

        mode = cfg.get("listen_mode", "wake")
        combo = (cfg.get("hotkey_combo") or "ctrl+alt+t").title().replace("+", "+")
        rec, strict = None, True
        wake_model = None
        wake = cfg["wake_lang"] if cfg["wake_lang"] in WAKE_GRAMMAR else "tr"
        sens = cfg.get("wake_sens", "normal")
        if mode != "wake":
            self.models.clear()
            gc.collect()
            core.log("Sürekli dinleme modu: Tank oturumu açık tutar." if mode == "continuous" else
                     f"Uyandırma kelimesi kapalı: daireye dokun veya {combo}.")
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
        elif mode == "wake":
            core.log(f"Uyandırma modeli yok: daireye tıkla veya {combo} kullan.")

        aq = queue.Queue()

        def cb(indata, frames, t, status):
            aq.put(bytes(indata))

        noise = deque(maxlen=80)
        recent = deque(maxlen=8)
        ring = deque(maxlen=16)
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
                ring.append(data)
                arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(arr ** 2)))
                core.state["level"] = min(1.0, rms / 3000.0)

                if self.manual.is_set():
                    self.manual.clear()
                    core.log("Elle başlatıldı (daire / kısayol).")
                    self.start_live(aq, rec, noise, None, True)
                    last, last_data, hits = None, time.time(), 0
                    continue
                if mode == "continuous":
                    noise.append(rms)
                    if not cfg.get("listening", True):
                        if last != "pause":
                            core.set_state("pause", "Dinleme durduruldu (daireye dokun)")
                            last = "pause"
                        continue
                    if time.time() < self.next_try:
                        if last != "retry":
                            core.set_state("wait", "Yeniden bağlanılıyor...")
                            last = "retry"
                        continue
                    self.start_live(aq, None, noise, None, True)
                    self.fail = 0 if core.live.last_ok else self.fail + 1
                    self.next_try = time.time() + (1.5 if core.live.last_ok else min(30, 2 ** min(self.fail, 5)))
                    last, last_data = None, time.time()
                    continue
                listening = cfg.get("listening", True) and rec is not None
                if not listening:
                    if last != "pause":
                        core.set_state("pause", "Dinleme kapalı" if rec is not None else f"Daireye dokun veya {combo}")
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
                    self.start_live(aq, rec, noise, list(ring), False)
                    last, last_data = None, time.time()


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
        self.preroll = []
        self.manual_start = False
        self.activity = False
        self.skip_lang = False
        self.skip_vocab = False
        self._tasks = set()
        self.last_ok = False
        self._ever = False

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

    def run(self, aq, thr, preroll=None, manual=False):
        core = self.core
        if self.active:
            return
        self._ever = False
        self._tasks = set()
        self.preroll = list(preroll or [])
        self.manual_start = bool(manual)
        self.activity = False
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
            self.last_ok = self._ever
            self.active = False
            self.speaking = False
            self._drain()
            core.state["level_out"] = 0.0
            core.close_chat()

    def _config(self, model=""):
        core, cfg = self.core, self.core.cfg
        speech = {"voice_config": {"prebuilt_voice_config": {"voice_name": cfg.get("voice", "Charon")}}}
        lang = LANGS.get(cfg.get("language", "tr"))
        if lang and not self.skip_lang:
            speech["language_code"] = lang[0]
        transcription = {}
        if "3.8" in model and not self.skip_vocab:
            vocab = build_vocab(core)
            if vocab:
                transcription["custom_vocabulary"] = vocab
        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": system_prompt(core, self.manual_start),
            "input_audio_transcription": transcription,
            "output_audio_transcription": {},
            "speech_config": speech,
        }
        tools = []
        decls = build_declarations(core)
        if decls:
            tools.append({"function_declarations": decls})
        if cfg.get("google_search", True):
            tools.append({"google_search": {}})
        if tools:
            config["tools"] = tools
        return config

    async def _session_retry(self, client, model, aq, thr, types, probe):
        core = self.core
        while True:
            try:
                await self._session(client, model, self._config(model), aq, thr, types, probe)
                return
            except Exception as e:
                low = str(e).lower()
                hard = any(x in low for x in ("api key", "api_key", "permission", "quota", "resource_exhausted", "429",
                                              "403", "404", "is not found", "models/", "location is not supported",
                                              "billing"))
                if self._connected or hard:
                    raise
                if "3.8" in model and not self.skip_vocab and build_vocab(core):
                    self.skip_vocab = True
                    core.log("Model özel kelime listesini kabul etmedi; o olmadan devam ediliyor.")
                elif core.cfg.get("google_search", True):
                    core.cfg["google_search"] = False
                    core.save_cfg()
                    core.log("Google araması bu modelde kullanılamadı; kapatıldı.")
                elif LANGS.get(core.cfg.get("language", "tr")) and not self.skip_lang:
                    self.skip_lang = True
                    core.log("Model dil kodunu kabul etmedi; dil yalnızca talimatla zorlanacak.")
                else:
                    raise

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
        from google import genai
        from google.genai import types
        core, cfg = self.core, self.core.cfg
        clients = {}

        def client_for(ver):
            if ver not in clients:
                clients[ver] = (genai.Client(api_key=key) if not ver else
                                genai.Client(api_key=key, http_options=types.HttpOptions(api_version=ver)))
            return clients[ver]

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
                    await self._session_retry(client_for(ver), model, aq, thr, types, probe)
                    if cfg.get("live_model") != model or (cfg.get("api_version") or "") != ver:
                        cfg["live_model"], cfg["api_version"] = model, ver
                        core.save_cfg()
                        core.log(f"Bağlantı ayarı kaydedildi: model={model}, sürüm={ver or 'varsayılan'}")
                    return model
                except Exception as e:
                    low = str(e).lower()
                    if self._connected and time.time() - t0 > 4:
                        raise
                    errors.append(f"{model} [{ver or 'varsayılan'}]: {_short(e, 150)}")
                    core.log("Bağlanamadı: " + errors[-1])
                    if self._connected:
                        good_ver = ver
                    handshake404 = (not self._connected) and "404" in low
                    modelish = any(x in low for x in ("not found", "not supported", "unsupported", "1008"))
                    if handshake404:
                        continue
                    all404 = False
                    if modelish:
                        break
                    raise RuntimeError(_short(e, 400))
            if all404:
                hs404 += 1
                if hs404 >= 2:
                    break
        raise RuntimeError("Live bağlantısı kurulamadı. " + " | ".join(errors[-4:]))

    async def _session(self, client, model, config, aq, thr, types, probe):
        core = self.core
        core.set_state("connecting", "Bağlanıyor...")
        async with client.aio.live.connect(model=model, config=config) as session:
            self._connected = True
            self._ever = True
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
                for t in list(self._tasks):
                    t.cancel()
                await asyncio.gather(*list(self._tasks), return_exceptions=True)
                self._stop_player()
        core.log("Oturum kapandı.")

    async def _send(self, session, aq, thr, types):
        core, cfg = self.core, self.core.cfg
        idle = float(cfg.get("idle_sec", 12))
        barge = bool(cfg.get("barge_in", False))
        cont = cfg.get("listen_mode") == "continuous"
        vad_ring = deque(maxlen=6)
        for d in self.preroll:
            await session.send_realtime_input(audio=types.Blob(data=d, mime_type="audio/pcm;rate=16000"))
        self.preroll = []
        while not core.engine.stop.is_set():
            if self.stop_flag:
                core.log("Oturum elle kapatıldı.")
                return
            busy = self.speaking or not self.pq.empty() or self.tool_busy > 0
            if self.end_flag and not busy:
                return
            now = time.time()
            quiet = now - max(self.last_voice, self.last_model, self.last_audio)
            idle_now = idle if self.activity else min(idle, 8.0)
            if not cont and ((not busy and quiet > idle_now) or quiet > idle + 90):
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
            if busy and not barge:
                continue
            if rms > thr:
                self.last_voice = now
                self.activity = True
            if cont and rms <= thr and now - self.last_voice > 1.5:
                vad_ring.append(data)
                continue
            if cont and vad_ring:
                for d in vad_ring:
                    await session.send_realtime_input(audio=types.Blob(data=d, mime_type="audio/pcm;rate=16000"))
                vad_ring.clear()
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
                            if d and d.data:
                                self.speaking = True
                                self.activity = True
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
                    task = asyncio.create_task(self._tools(session, tc, types))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                if getattr(resp, "go_away", None):
                    core.log("Gemini: bağlantı yakında kapanacak.")

    async def _tools(self, session, tc, types):
        core = self.core
        try:
            resps = []
            for fc in tc.function_calls:
                args = dict(fc.args or {})
                label = TOOLS[fc.name]["label"] if fc.name in TOOLS else fc.name
                core.set_state("think", f"Çalıştırıyor: {label}")
                res = await asyncio.to_thread(run_tool, core, fc.name, args)
                resps.append(types.FunctionResponse(id=fc.id, name=fc.name, response=res))
            await session.send_tool_response(function_responses=resps)
            self.last_model = time.time()
            if not self.speaking:
                core.set_state("listen", "Dinliyorum")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            core.log(f"Araç yanıtı gönderilemedi: {_short(e)}")
        finally:
            self.tool_busy -= 1

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


INT_KEYS = {"beep_volume", "idle_sec"}
BOOL_KEYS = {"barge_in", "listening", "strict_wake", "hotkey", "feedback_sounds", "onboarded", "use_memory",
             "sidebar_collapsed", "close_to_tray", "log_to_file", "advanced_settings"}
STR_KEYS = {"live_model", "voice", "wake_lang", "wake_sens", "beep_sound", "wake_mode", "listen_mode", "language",
            "custom_instructions", "hotkey_combo", "weather_location"}


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
        langs = [{"id": "auto", "name": "Otomatik algıla"}] + [{"id": k, "name": v[1]} for k, v in LANGS.items()]
        return {"memory": self.get_memory(), "langs": langs, "data_dir": str(DATA),
                "shortcuts": c.shortcuts, "config": self._public_cfg(), "mics": mics, "outs": outs,
                "voices": VOICES, "tools": self._tools(), "autostart": get_autostart(),
                "version": VERSION, "has_key": bool(c.get_key())}

    def _public_cfg(self):
        return {k: v for k, v in self._core.cfg.items() if k not in ("api_key_enc", "tools", "cfg_ver")}

    def _tools(self):
        c = self._core
        return [{"name": t["name"], "label": t["label"], "desc": t["desc"], "danger": t["danger"],
                 "group": TOOL_GROUPS.get(t["name"], "Diğer"), "enabled": c.tool_enabled(t["name"])} for t in TOOL_DEFS]

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
        s["listening"] = c.cfg.get("listening", True)
        s["mode"] = c.cfg.get("listen_mode", "wake")
        return s

    def talk(self):
        self._core.talk()
        return True

    def confirm_answer(self, cid, ok):
        self._core.answer_confirm(cid, ok)
        return True

    def get_memory(self):
        return [{"key": m["key"], "value": m["value"]} for m in self._core.memory]

    def add_memory(self, key, value):
        self._core.mem_set(key, value)
        return self.get_memory()

    def delete_memory(self, key):
        self._core.mem_delete(key)
        return self.get_memory()

    def clear_memory(self):
        self._core.memory = []
        save_json(MEMORY_PATH, {"items": []})
        return []

    def get_reminders(self):
        return [{"id": r.get("id"), "text": r["text"], "due": datetime.datetime.fromtimestamp(r["due"]).strftime("%d.%m %H:%M")}
                for r in self._core.reminders if not r.get("done")]

    def delete_reminder_id(self, rid):
        c = self._core
        c.reminders = [r for r in c.reminders if r.get("id") != rid]
        c.save_reminders()
        return self.get_reminders()

    def open_data_folder(self):
        DATA.mkdir(parents=True, exist_ok=True)
        os.startfile(str(DATA))
        return True

    def open_log(self):
        if LOG_PATH.exists():
            os.startfile(str(LOG_PATH))
            return True
        return False

    def reset_settings(self):
        c = self._core
        keep = {k: c.cfg.get(k) for k in ("api_key_enc", "onboarded")}
        c.cfg.clear()
        c.cfg.update(DEFAULT_CONFIG)
        c.cfg["tools"] = {}
        c.cfg.update(keep)
        c.save_cfg()
        c.engine.reopen.set()
        return self._public_cfg()

    def set_key(self, key, portable=False):
        self._core.set_key(key, bool(portable))
        return bool(self._core.get_key())

    def open_external(self, url):
        if isinstance(url, str) and url.startswith("https://aistudio.google.com/"):
            os.startfile(url)
            return True
        return False

    def test_live(self):
        c = self._core
        key = c.get_key()
        if not key:
            return {"ok": False, "error": "Önce anahtarı kaydet."}
        c.cfg["google_search"] = True
        c.live.skip_lang = False
        c.live.skip_vocab = False
        try:
            model = asyncio.run(c.live.connect_loop(key, None, 0.0, probe=True))
            c.set_state("wait", "“Hey Tank” bekleniyor")
            return {"ok": True, "model": model, "search": bool(c.cfg.get("google_search", True))}
        except Exception as e:
            return {"ok": False, "error": _short(e, 600)}

    def test_models(self):
        c = self._core
        key = c.get_key()
        if not key:
            return {"ok": False, "error": "Önce anahtarı kaydet."}
        try:
            names = rank_live_models(list_live_models(key)["live"])
            for m in LIVE_FALLBACK_MODELS:
                if m not in names:
                    names.append(m)
            res = asyncio.run(probe_models(key, names, c.cfg.get("api_version") or ""))
            return {"ok": True, "results": res, "current": c.cfg.get("live_model", "")}
        except Exception as e:
            return {"ok": False, "error": _short(e, 600)}

    def run_diagnostics(self):
        c = self._core
        out = []

        def add(name, status, detail):
            out.append({"name": name, "status": status, "detail": detail})

        key = c.get_key()
        add("Gemini anahtarı", "ok" if key else "fail", "Kayıtlı." if key else "Anahtar yok; Ayarlar'dan ekle.")
        try:
            import sounddevice as sd
            levels = []
            with sd.RawInputStream(samplerate=SR, blocksize=2000, device=c.cfg.get("mic_device"),
                                   dtype="int16", channels=1) as st:
                t0 = time.time()
                while time.time() - t0 < 0.8:
                    data, _ = st.read(2000)
                    a = np.frombuffer(bytes(data), dtype=np.int16).astype(np.float32)
                    levels.append(float(np.sqrt(np.mean(a ** 2))))
            peak = max(levels) if levels else 0
            if peak > 5:
                add("Mikrofon", "ok", f"Ses alınıyor (seviye {int(peak)}).")
            else:
                add("Mikrofon", "warn", "Ses çok zayıf ya da yok; mikrofon seçimini ve Windows ses ayarını kontrol et.")
        except Exception as e:
            add("Mikrofon", "fail", _short(e, 140))
        try:
            import sounddevice as sd
            sd.query_devices(kind="output")
            play_sound("ok", max(40, c.cfg.get("beep_volume", 60)))
            add("Hoparlör", "ok", "Test sesi çalındı; duyduysan sorun yok.")
        except Exception as e:
            add("Hoparlör", "fail", _short(e, 140))
        try:
            import urllib.request
            import urllib.error
            try:
                urllib.request.urlopen("https://generativelanguage.googleapis.com/", timeout=6)
            except urllib.error.HTTPError:
                pass
            add("İnternet", "ok", "Gemini sunucusuna ulaşılıyor.")
        except Exception as e:
            add("İnternet", "fail", "Gemini sunucusuna ulaşılamıyor: " + _short(e, 100))
        if not key:
            add("Model bağlantısı", "fail", "Anahtar olmadan test edilemez.")
        elif c.live.active:
            add("Model bağlantısı", "warn", "Oturum açıkken test edilemez; oturumu kapatıp tekrar dene.")
        else:
            r = self.test_live()
            if r["ok"]:
                add("Model bağlantısı", "ok", f"Bağlandı: {r['model']}")
                add("Google araması", "ok" if r["search"] else "warn",
                    "Açık." if r["search"] else "Bu modelde kullanılamıyor, kapalı.")
            else:
                add("Model bağlantısı", "fail", hint_for_error(r["error"]))
        mode = c.cfg.get("listen_mode", "wake")
        if mode == "wake":
            ok = (MODELS / ("vosk-tr" if c.cfg.get("wake_lang", "tr") == "tr" else "vosk-en")).exists()
            add("Uyandırma modeli", "ok" if ok else "fail", "Yüklü." if ok else "models klasöründe uyandırma modeli yok.")
        else:
            add("Uyandırma modeli", "ok", "Bu modda gerekmiyor.")
        hk = c.engine.hotkey_ok
        add("Genel kısayol", "ok" if hk else ("warn" if hk is None else "fail"),
            f"{c.cfg.get('hotkey_combo')} kayıtlı." if hk else "Kayıtlı değil; başka bir uygulama bu tuşu kullanıyor olabilir.")
        add("Pencere", "ok" if self._app.window is not None else "warn",
            "Normal pencere." if self._app.window is not None else "Edge penceresi modu (WebView2 yüklenemedi).")
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            probe_file = DATA / ".yazma-testi"
            probe_file.write_text("1")
            probe_file.unlink()
            add("Veri klasörü", "ok", str(DATA))
        except Exception as e:
            add("Veri klasörü", "fail", "Yazılamıyor: " + _short(e, 100))
        ok, txt = appvol_ps("list")
        add("Uygulama sesleri", "ok" if ok else "warn",
            "Ses karıştırıcıya erişiliyor." if ok else "Ses karıştırıcıya erişilemedi: " + txt[:100])
        try:
            from PIL import ImageGrab
            ImageGrab.grab()
            add("Ekran görüntüsü", "ok", "Alınabiliyor.")
        except Exception as e:
            add("Ekran görüntüsü", "fail", _short(e, 100))
        return out


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
                threading.Thread(target=run_shortcut_obj, args=(x, self._core.log), daemon=True).start()
        return True

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
            if key == "listen_mode" and value not in ("wake", "continuous", "tap"):
                return False
            if key == "language" and value != "auto" and value not in LANGS:
                return False
            if key == "hotkey_combo":
                value = value.strip().lower()
                if hotkey_parts(value) == (0, 0):
                    return False
            if key == "language":
                c.live.skip_lang = False
        c.cfg[key] = value
        c.save_cfg()
        if key in ("mic_device", "wake_lang", "strict_wake", "wake_sens", "wake_mode", "listen_mode"):
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


def unblock_files():
    try:
        for root in {BASE, RES}:
            for dp, dn, fn in os.walk(root):
                dn[:] = [d for d in dn if d not in ("models", "webview-data", "data")]
                for f in fn:
                    if f.lower().endswith((".dll", ".pyd", ".exe", ".config")):
                        try:
                            os.remove(os.path.join(dp, f) + ":Zone.Identifier")
                        except OSError:
                            pass
    except Exception:
        pass


def selftest():
    out = BASE / "selftest.txt"
    code = 0
    try:
        unblock_files()
        import clr
        import webview
        from google import genai
        from google.genai import types
        import websockets
        import vosk
        import sounddevice
        import psutil
        from PIL import ImageGrab
        out.write_text("OK", encoding="utf-8")
    except Exception as e:
        out.write_text(f"HATA: {e!r}", encoding="utf-8")
        code = 1
    os._exit(code)


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


class App:
    def __init__(self, hidden):
        self.core = Core()
        self.hidden = hidden
        self.window = None
        self.tray = None
        self.httpd = None
        self.proc = None

    def set_window_icon(self, *a):
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
                             f"--user-data-dir={DATA / 'webview' / 'browser'}",
                             "--window-size=1100,720", "--no-first-run",
                             "--no-default-browser-check"])
                        return
                    except Exception as e:
                        self.core.log(f"Tarayıcı açılamadı: {e!r}")
        import webbrowser
        webbrowser.open(url)

    def watch_show(self):
        f = DATA / ".show"
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
        if self.tray and self.core.cfg.get("close_to_tray", True):
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
        unblock_files()
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HeyTank.App")
        except Exception:
            pass
        if get_autostart():
            try:
                set_autostart(True)
            except Exception:
                pass
        self.core.engine.start()
        self.setup_tray()
        self.core.attention = self.show
        self.core.start_background()
        self.core.notify = self.notify
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
                webview.start(gui="edgechromium", storage_path=str(DATA / "webview"))
                ok = True
            except Exception as e:
                self.window = None
                self.core.log(f"Pencere modu açılamadı ({e!r}). Tarayıcı moduna geçildi.")
        if ok:
            self.quit()

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
            DATA.mkdir(parents=True, exist_ok=True)
            (DATA / ".show").write_text("1")
        except Exception:
            pass
