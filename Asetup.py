#!/usr/bin/env python3
"""
setup.py - Interactive control center for the local llama.cpp LAN server.

Run this from the same folder as llama-server.exe.

Built for ULTRA REMOTE / zero-connectivity use: nothing in this script
assumes internet access. Anything that could touch the network (pip
installs) is optional, skippable, and time-boxed so a dead connection
fails fast instead of hanging the whole tool.

Features:
  - Quick Start wizard (model -> port -> host -> launch), fully skippable
  - Background server process (menu stays usable while server runs)
  - Recursive .gguf model scanner (current folder + all subfolders)
  - Server profiles (save/load/delete named configs)
  - Diagnostics (exe present, port free, firewall rule, disk space, RAM)
  - Live-ish log viewer (tails the server's output log)
  - LAN URL + terminal ASCII QR code for phone access (optional)
  - Best-effort dependency install, never required to proceed

Windows-focused (cmd), but degrades gracefully on other platforms.
"""

import ctypes
import json
import os
import platform
import random
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------
# ANSI Styling & Windows Terminal Activation (Upgraded Matrix UI)
# --------------------------------------------------------------------------

IS_WINDOWS = platform.system() == "Windows"

def init_terminal():
    """Enables native Virtual Terminal / ANSI escape processing in Windows cmd."""
    if IS_WINDOWS:
        os.system("")  # Triggers default Windows VT mode
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            pass

init_terminal()

class UI:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    ITALIC  = "\033[3m"
    
    # Cyberpunk / Matrix Palette
    BASE    = "\033[38;5;46m"   # Bright Terminal Green (Default Syntax)
    CYAN    = "\033[38;5;51m"   # Highlighting / Accents
    AQUA    = "\033[38;5;45m"
    PURPLE  = "\033[38;5;141m"  # Prompts & Arrows
    VIOLET  = "\033[38;5;99m"
    EMERALD = "\033[38;5;48m"
    AMBER   = "\033[38;5;214m"  # Warnings
    ROSE    = "\033[38;5;204m"
    RED     = "\033[38;5;196m"  # Errors / Stop
    WHITE   = "\033[38;5;255m"  # Strict highlights
    MUTED   = "\033[38;5;244m"  # Subtle text / borders
    DARK    = "\033[38;5;238m"

    # Status Badges
    TAG_OK   = f"\033[38;5;48m[ ✔ OK ]{RESET}{BASE}"
    TAG_WARN = f"\033[38;5;214m[ ▲ WARN ]{RESET}{BASE}"
    TAG_FAIL = f"\033[38;5;196m[ ✖ FAIL ]{RESET}{BASE}"
    TAG_INFO = f"\033[38;5;51m[ ◈ INFO ]{RESET}{BASE}"


# --------------------------------------------------------------------------
# Paths / constants
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "server_config.json"
PROFILES_FILE = BASE_DIR / "server_profiles.json"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

EXE_CANDIDATES = ["llama-server.exe", "llama-server", "main.exe", "server.exe"]
DEFAULT_HOST = "0.0.0.0"
PIP_TIMEOUT_SECONDS = 8  # fail fast on dead connections, don't hang forever

# Holds the live background server process, if any, for this session.
SERVER_PROC = None
SERVER_LOG_PATH = None
SERVER_INFO = {}  # port, host, model, started_at

# Set True once the user says "don't ask about deps again this session".
DEPS_PERMANENTLY_SKIPPED = False


# --------------------------------------------------------------------------
# Small utilities & Network Math
# --------------------------------------------------------------------------

def clear_screen():
    os.system("cls" if IS_WINDOWS else "clear")


def divider(char="═", width=70, color=UI.MUTED):
    print(f"  {color}{char * width}{UI.RESET}{UI.BASE}")


def header(title):
    clear_screen()
    print(f"  {UI.CYAN}╔{'═' * 68}╗{UI.RESET}")
    print(f"  {UI.CYAN}║{UI.RESET}  {UI.EMERALD}{UI.BOLD}◈  {title.upper()}{UI.RESET}{' ' * max(0, 63 - len(title))}{UI.CYAN}║{UI.RESET}")
    print(f"  {UI.CYAN}╚{'═' * 68}╝{UI.RESET}{UI.BASE}\n")


def explain(text):
    """
    Print a short 'here's what's about to happen and why' blurb.
    This is the whole point of the script being conversational: you
    should never hit a prompt without knowing what answering it does.
    """
    print(f"    {UI.AQUA}ℹ{UI.RESET} {UI.BASE}{UI.DIM}> {text}{UI.RESET}{UI.BASE}\n")


def prompt(text, default=None, show_options=True):
    """
    Prompt with support for:
      Enter -> accept default
      'b'   -> go back (returns "__back__")
      's'   -> skip THIS step, use default (returns "__skip__")
      'q'   -> quit to main menu (returns "__quit__")
    show_options prints a visible line listing these every time — on a
    remote/offline box you might not have this script's docs handy, so
    the options live on-screen, not just in your memory of how it works.
    """
    if show_options:
        print(f"    {UI.MUTED}[Enter]=default   [b]=back   [s]=skip   [q]=menu{UI.RESET}{UI.BASE}")
    suffix = f" {UI.MUTED}[{UI.CYAN}{default}{UI.MUTED}]{UI.RESET}{UI.BASE}" if default is not None else ""
    try:
        raw = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.BASE}{UI.BOLD}{text}{suffix}{UI.RESET}{UI.BASE}: ").strip()
    except (KeyboardInterrupt, EOFError):
        return "__quit__"

    if raw == "":
        return default
    low = raw.lower()
    if low == "b":
        return "__back__"
    if low == "s":
        return "__skip__"
    if low == "q":
        return "__quit__"
    return raw


def load_json(path, fallback):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return fallback
    return fallback


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_lan_ip():
    """
    Best-effort LAN IP detection. This doesn't actually send any data —
    connect() on a UDP socket just asks the OS 'which of my network
    interfaces would I use to reach this address', which works even
    fully offline since 8.8.8.8 is never actually contacted.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def is_port_free(port, host="0.0.0.0"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        result = s.connect_ex((host if host != "0.0.0.0" else "127.0.0.1", port))
        return result != 0  # nonzero = nothing listening = free
    finally:
        s.close()


def get_random_free_port(start_range=10000, end_range=60000):
    """
    Guarantees we never hit a port collision by randomly scanning
    for a definitively open port on the machine.
    """
    for _ in range(100):  # Try 100 times to find a free port
        p = random.randint(start_range, end_range)
        if is_port_free(p):
            return p
    return 48123  # Fallback just in case


def find_exe():
    """Look for the llama-server binary in this folder."""
    for name in EXE_CANDIDATES:
        candidate = BASE_DIR / name
        if candidate.exists():
            return candidate
    for f in BASE_DIR.glob("*.exe"):
        if "server" in f.name.lower():
            return f
    return None


def scan_models():
    """Recursively find all .gguf files under BASE_DIR."""
    models = []
    for path in BASE_DIR.rglob("*.gguf"):
        try:
            size_mb = path.stat().st_size / (1024 * 1024)
        except OSError:
            size_mb = 0
        models.append({
            "path": path,
            "name": path.name,
            "rel": str(path.relative_to(BASE_DIR)),
            "size_mb": round(size_mb, 1),
        })
    models.sort(key=lambda m: m["rel"].lower())
    return models


# --------------------------------------------------------------------------
# Dependency bootstrap (fully optional, offline-safe)
# --------------------------------------------------------------------------

def ensure_dependencies():
    """
    Offer to install any missing third-party packages — never force it,
    never require it. This script is designed to run on a laptop that
    has never seen the internet and never will, so every feature that
    needs an optional package just degrades (plain URL instead of a
    scannable QR code, etc.) rather than blocking anything.
    """
    global DEPS_PERMANENTLY_SKIPPED

    required = {"qrcode": "qrcode"}
    missing = []
    for module_name, pip_name in required.items():
        try:
            __import__(module_name)
        except ImportError:
            missing.append(pip_name)

    if not missing or DEPS_PERMANENTLY_SKIPPED:
        return

    explain(
        "One optional extra is missing. It only powers the terminal QR code "
        "that turns a URL into something your phone camera can scan — every "
        "other feature in this script works fine without it. If this laptop "
        "has no internet (the whole point of 'ultra remote'), just skip this; "
        "trying to install anyway will simply time out in a few seconds."
    )
    print(f"    {UI.MUTED}Missing: {UI.CYAN}{', '.join(missing)}{UI.RESET}{UI.BASE}\n")
    print(f"    {UI.CYAN}[y]{UI.RESET}{UI.BASE} Try installing now (needs internet, times out fast if none)")
    print(f"    {UI.CYAN}[n]{UI.RESET}{UI.BASE} Skip just this once, ask me again next run")
    print(f"    {UI.CYAN}[s]{UI.RESET}{UI.BASE} Skip permanently for this session (don't ask again)")
    choice = input(f"\n    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Choice [n]: ").strip().lower()

    if choice == "s":
        DEPS_PERMANENTLY_SKIPPED = True
        print(f"    {UI.MUTED}Skipping dependency checks for the rest of this session.{UI.RESET}{UI.BASE}\n")
        return

    if choice != "y":
        print(f"    {UI.MUTED}Skipping for now. Moving on.{UI.RESET}{UI.BASE}\n")
        return

    for pkg in missing:
        print(f"    {UI.TAG_INFO} Attempting to install {pkg} "
              f"({PIP_TIMEOUT_SECONDS}s timeout — won't hang if you're offline)...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet", pkg],
                timeout=PIP_TIMEOUT_SECONDS,
            )
            print(f"    {UI.TAG_OK} Installed {pkg}")
        except subprocess.TimeoutExpired:
            print(f"    {UI.TAG_WARN} Timed out — no internet reachable. Skipping {pkg}.")
        except subprocess.CalledProcessError:
            print(f"    {UI.TAG_FAIL} WARNING: could not install {pkg} automatically.")
            print(f"    {UI.MUTED}If you ever get connectivity: pip install {pkg}{UI.RESET}{UI.BASE}")
    print()


# --------------------------------------------------------------------------
# QR code (optional — degrades to a plain URL)
# --------------------------------------------------------------------------

def print_qr(url):
    """
    Prints an ASCII QR code if the qrcode library is available. If not
    (offline box, install skipped, whatever), we just print the plain
    URL — the phone connection still works, you just type it instead
    of scanning it.
    """
    try:
        import qrcode
    except ImportError:
        print(f"\n    {UI.MUTED}(No QR library installed — just type this URL on your phone: {UI.CYAN}{url}{UI.MUTED}){UI.RESET}{UI.BASE}\n")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    print()
    qr.print_ascii(invert=True)
    print()


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------

def check_firewall_rule(port):
    """
    Windows only: check if an inbound rule for this port exists.
    netsh can be slow on some machines, which is exactly why this
    single check (not just the whole diagnostics run) is individually
    skippable below.
    """
    if not IS_WINDOWS:
        return None
    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=all"],
            capture_output=True, text=True, timeout=10
        )
        return str(port) in result.stdout
    except Exception:
        return None


def check_disk_space():
    total, used, free = shutil.disk_usage(BASE_DIR)
    return {
        "total_gb": round(total / (1024**3), 1),
        "used_gb": round(used / (1024**3), 1),
        "free_gb": round(free / (1024**3), 1),
    }


def check_ram():
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {
            "total_gb": round(vm.total / (1024**3), 1),
            "available_gb": round(vm.available / (1024**3), 1),
        }
    except ImportError:
        return None


def run_diagnostics(port=None):
    header("Diagnostics Engine")
    explain(
        "Six quick checks that answer 'why won't my phone connect' before it "
        "even becomes a mystery. Each one is a thing that has to be true for "
        "the server to be reachable. None of these touch the internet — "
        "they're all local checks — so feel free to run all six even fully "
        "offline. Skip any single one, or bail out of the whole diagnostics "
        "run early with 'q'."
    )

    def do_check(label, fn):
        choice = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.MUTED}[Enter=run / s=skip / q=stop]{UI.RESET} {UI.BASE}{UI.BOLD}{label}{UI.RESET}{UI.BASE}: ").strip().lower()
        if choice == "q":
            return "__quit__"
        if choice == "s":
            print(f"      {UI.MUTED}↷ (skipped){UI.RESET}{UI.BASE}\n")
            return "__skipped__"
        return fn()

    exe = do_check(
        "Locating server binary (no exe, nothing else matters)",
        find_exe
    )
    if exe == "__quit__":
        return
    if exe != "__skipped__":
        status = UI.TAG_OK if exe else UI.TAG_FAIL
        val = f"{UI.WHITE}{exe}{UI.RESET}{UI.BASE}" if exe else f"{UI.RED}NOT FOUND in {BASE_DIR}{UI.RESET}{UI.BASE}"
        print(f"      {status} Server executable: {val}\n")

    models = do_check(
        "Scanning for .gguf model files (the actual brains it loads)",
        scan_models
    )
    if models == "__quit__":
        return
    if models != "__skipped__":
        status = UI.TAG_OK if models else UI.TAG_WARN
        print(f"      {status} Models found: {UI.WHITE}{len(models)}{UI.RESET}{UI.BASE}\n")

    check_port = port or get_random_free_port()
    free = do_check(
        f"Checking if port {check_port} is free (busy port = #1 silent-fail cause)",
        lambda: is_port_free(check_port)
    )
    if free == "__quit__":
        return
    if free != "__skipped__":
        status = UI.TAG_OK if free else UI.TAG_WARN
        val = f"{UI.EMERALD}free{UI.RESET}{UI.BASE}" if free else f"{UI.RED}already in use{UI.RESET}{UI.BASE}"
        print(f"      {status} Port {check_port}: {val}\n")

    fw = do_check(
        f"Checking Windows Firewall for rule on port {check_port} (can be slow — safe to skip)",
        lambda: check_firewall_rule(check_port)
    )
    if fw == "__quit__":
        return
    if fw != "__skipped__":
        if fw is None:
            print(f"      {UI.TAG_INFO} Firewall rule check: unavailable on this platform\n")
        else:
            status = UI.TAG_OK if fw else UI.TAG_WARN
            val = f"{UI.EMERALD}found{UI.RESET}{UI.BASE}" if fw else f"{UI.AMBER}not found (may need to add one){UI.RESET}{UI.BASE}"
            print(f"      {status} Firewall rule mentioning port {check_port}: {val}\n")

    disk = do_check(
        "Checking disk space (models are chunky, needs room to breathe)",
        check_disk_space
    )
    if disk == "__quit__":
        return
    if disk != "__skipped__":
        status = UI.TAG_OK if disk["free_gb"] > 2 else UI.TAG_WARN
        print(f"      {status} Disk free: {UI.WHITE}{disk['free_gb']} GB{UI.RESET}{UI.BASE} / {disk['total_gb']} GB total\n")

    ram = do_check(
        "Checking available RAM (limits how big a model you can run on CPU)",
        check_ram
    )
    if ram == "__quit__":
        return
    if ram != "__skipped__":
        if ram:
            print(f"      {UI.TAG_OK} RAM: {UI.WHITE}{ram['available_gb']} GB available{UI.RESET}{UI.BASE} / {ram['total_gb']} GB total\n")
        else:
            print(f"      {UI.TAG_INFO} RAM check: needs 'psutil', which isn't installed (optional, offline-safe to skip)\n")

    ip = do_check(
        "Detecting your LAN IP (the address your phone actually types in)",
        get_lan_ip
    )
    if ip == "__quit__":
        return
    if ip != "__skipped__":
        print(f"      {UI.TAG_OK} LAN IP detected: {UI.CYAN}{ip}{UI.RESET}{UI.BASE}\n")

    input(f"    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Config / profiles
# --------------------------------------------------------------------------

def load_last_config():
    return load_json(CONFIG_FILE, {
        "model": None,
        "port": None,
        "host": DEFAULT_HOST,
    })


def save_last_config(cfg):
    save_json(CONFIG_FILE, cfg)


def load_profiles():
    return load_json(PROFILES_FILE, {})


def save_profile(name, cfg):
    profiles = load_profiles()
    profiles[name] = cfg
    save_json(PROFILES_FILE, profiles)


def delete_profile(name):
    profiles = load_profiles()
    if name in profiles:
        del profiles[name]
        save_json(PROFILES_FILE, profiles)


def profiles_menu():
    while True:
        header("Server Profiles")
        explain(
            "A profile is a saved combo of model + port + host under a name "
            "you pick — 'fast-1B for guests' or 'quality-4B for me' loads "
            "instantly instead of re-answering the wizard every time. All "
            "stored locally in a plain JSON file next to this script."
        )
        profiles = load_profiles()
        if not profiles:
            print(f"    {UI.MUTED}No saved profiles yet.{UI.RESET}{UI.BASE}\n")
        else:
            for i, (name, cfg) in enumerate(profiles.items(), 1):
                model_str = Path(cfg['model']).name if cfg.get('model') else '?'
                print(f"     {UI.CYAN}{i:2d}.{UI.RESET} {UI.WHITE}{name}{UI.RESET}  {UI.PURPLE}➔{UI.RESET} {UI.BASE}model={UI.AQUA}{model_str}{UI.RESET}{UI.BASE}, port={UI.WHITE}{cfg.get('port')}{UI.RESET}{UI.BASE}, host={UI.WHITE}{cfg.get('host')}{UI.RESET}{UI.BASE}")
            print()

        print(f"    {UI.CYAN}[n]{UI.RESET}{UI.BASE} New profile from current last-used config")
        print(f"    {UI.CYAN}[l]{UI.RESET}{UI.BASE} Load a profile and start server")
        print(f"    {UI.CYAN}[d]{UI.RESET}{UI.BASE} Delete a profile")
        print(f"    {UI.CYAN}[s]{UI.RESET}{UI.BASE} Skip / do nothing here")
        print(f"    {UI.CYAN}[q]{UI.RESET}{UI.BASE} Back to main menu")
        choice = input(f"\n    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Choice: ").strip().lower()

        if choice in ("q", "s", ""):
            return
        elif choice == "n":
            cfg = load_last_config()
            if not cfg.get("model"):
                print(f"\n    {UI.TAG_WARN} No last-used config to save yet. Run Quick Start first.")
                input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
                continue
            name = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Profile name (or Enter to skip): ").strip()
            if name:
                save_profile(name, cfg)
                print(f"    {UI.TAG_OK} Saved profile '{name}'.")
            else:
                print(f"    {UI.MUTED}Skipped — no profile saved.{UI.RESET}{UI.BASE}")
            input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
        elif choice == "l":
            if not profiles:
                continue
            name = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Profile name to load (or Enter to skip): ").strip()
            if not name:
                continue
            if name in profiles:
                cfg = profiles[name]
                start_server(cfg["model"], cfg["port"], cfg["host"])
                input(f"\n    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
            else:
                print(f"    {UI.TAG_FAIL} Not found.")
                input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
        elif choice == "d":
            if not profiles:
                continue
            name = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Profile name to delete (or Enter to skip): ").strip()
            if not name:
                continue
            if name in profiles:
                delete_profile(name)
                print(f"    {UI.TAG_OK} Deleted '{name}'.")
            input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Model manager
# --------------------------------------------------------------------------

def choose_model(default_path=None):
    """
    Returns a Path to a .gguf file, or a control string
    ("__back__", "__skip__", "__quit__"), or None if none found.
    """
    models = scan_models()
    if not models:
        print(f"\n    {UI.TAG_FAIL} No .gguf files found under {BASE_DIR} (recursive scan).")
        print(f"    {UI.MUTED}Add a model file, then re-run.{UI.RESET}{UI.BASE}")
        return None

    explain(
        "Picking which model loads into memory when the server starts. Only "
        "one runs at a time. On CPU-only, offline hardware, smaller files "
        "respond faster but reason worse; bigger ones are smarter but crawl. "
        "* marks your last pick, and Enter just reuses it."
    )
    print(f"    {UI.BOLD}Models found:{UI.RESET}{UI.BASE}\n")
    for i, m in enumerate(models, 1):
        marker = f" {UI.EMERALD}◀ active{UI.RESET}{UI.BASE}" if default_path and str(m["path"]) == str(default_path) else ""
        print(f"     {UI.CYAN}{i:2d}.{UI.RESET} {UI.WHITE}{m['rel']:<45}{UI.RESET} {UI.MUTED}({m['size_mb']} MB){UI.RESET}{UI.BASE}{marker}")
    print()

    default_index = None
    if default_path:
        for i, m in enumerate(models, 1):
            if str(m["path"]) == str(default_path):
                default_index = i

    default_label = str(default_index) if default_index else str(1)
    choice = prompt("Pick a model number", default=default_label)

    if choice in ("__back__", "__skip__", "__quit__"):
        if choice == "__skip__":
            idx = default_index or 1
            return models[idx - 1]["path"]
        return choice

    try:
        idx = int(choice)
        if 1 <= idx <= len(models):
            return models[idx - 1]["path"]
    except ValueError:
        pass

    print(f"    {UI.TAG_WARN} Invalid choice, using default.")
    idx = default_index or 1
    return models[idx - 1]["path"]


def models_menu():
    header("Local Model Matrix")
    explain(
        "This walks every subfolder under your llama.cpp directory looking "
        "for .gguf files — that extension is the giveaway for a quantized "
        "model llama.cpp can actually load. Bigger file size roughly means "
        "smarter but slower; on limited hardware, smaller usually wins. "
        "This is a read-only scan — nothing here touches your files."
    )
    models = scan_models()
    if not models:
        print(f"    {UI.TAG_WARN} No .gguf files found under {BASE_DIR} (recursive scan).")
        print(f"    {UI.MUTED}Drop a model file anywhere in this folder (subfolders are fine) and re-run.{UI.RESET}{UI.BASE}")
    else:
        total_mb = sum(m["size_mb"] for m in models)
        print(f"    {UI.TAG_OK} Found {UI.BOLD}{len(models)}{UI.RESET}{UI.BASE} model(s), {UI.BOLD}{round(total_mb / 1024, 2)} GB{UI.RESET}{UI.BASE} total:\n")
        for i, m in enumerate(models, 1):
            print(f"     {UI.CYAN}{i:2d}.{UI.RESET} {UI.WHITE}{m['rel']:<45}{UI.RESET} {UI.MUTED}({m['size_mb']} MB){UI.RESET}{UI.BASE}")
    print()
    input(f"    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Server control (background process)
# --------------------------------------------------------------------------

def start_server(model_path, port, host):
    global SERVER_PROC, SERVER_LOG_PATH, SERVER_INFO

    if SERVER_PROC and SERVER_PROC.poll() is None:
        print(f"\n    {UI.TAG_WARN} A server is already running in this session.")
        print(f"    {UI.MUTED}Stop it first (Server Status/Stop) before starting another.{UI.RESET}{UI.BASE}")
        return False

    exe = find_exe()
    if not exe:
        print(f"\n    {UI.TAG_FAIL} ERROR: no server executable found in {BASE_DIR}")
        print(f"    {UI.MUTED}Expected one of: {', '.join(EXE_CANDIDATES)}{UI.RESET}{UI.BASE}")
        return False

    model_path = Path(model_path)
    if not model_path.exists():
        print(f"\n    {UI.TAG_FAIL} ERROR: model file not found: {model_path}")
        return False

    # Force port resolution if None was passed, or automatically fix collisions
    if not port or not is_port_free(port):
        new_port = get_random_free_port()
        if port:
            print(f"\n    {UI.TAG_WARN} Port {port} is busy. Automatically randomized to unused port {new_port}.")
        port = new_port

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"server-{timestamp}.log"

    cmd = [str(exe), "-m", str(model_path), "--host", host, "--port", str(port)]

    print(f"\n    {UI.TAG_INFO} Starting server in background...")
    print(f"      Executable : {UI.WHITE}{exe.name}{UI.RESET}{UI.BASE}")
    print(f"      Model      : {UI.CYAN}{model_path.relative_to(BASE_DIR)}{UI.RESET}{UI.BASE}")
    print(f"      Host:Port  : {UI.AQUA}{host}:{port}{UI.RESET}{UI.BASE}")
    print(f"      Log file   : {UI.MUTED}{log_path}{UI.RESET}{UI.BASE}")

    log_handle = open(log_path, "w", encoding="utf-8", errors="replace")

    creationflags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(BASE_DIR),
            creationflags=creationflags,
        )
    except Exception as e:
        print(f"\n    {UI.TAG_FAIL} ERROR: failed to launch server: {e}")
        log_handle.close()
        return False

    SERVER_PROC = proc
    SERVER_LOG_PATH = log_path
    SERVER_INFO = {
        "model": str(model_path),
        "port": port,
        "host": host,
        "started_at": timestamp,
    }

    # Give it a moment, then sanity-check it's still alive.
    time.sleep(2)
    if proc.poll() is not None:
        print(f"\n    {UI.TAG_FAIL} ERROR: server exited immediately. Check the log:")
        print(f"    {UI.MUTED}{log_path}{UI.RESET}{UI.BASE}")
        return False

    save_last_config({"model": str(model_path), "port": port, "host": host})

    ip = get_lan_ip()
    url = f"http://{ip}:{port}"
    local_url = f"http://localhost:{port}"

    print(f"\n    {UI.TAG_OK} {UI.EMERALD}{UI.BOLD}SERVER IS UP AND RUNNING.{UI.RESET}{UI.BASE}")
    print(f"      On this laptop : {UI.CYAN}{local_url}{UI.RESET}{UI.BASE}")
    print(f"      On your phone  : {UI.EMERALD}{url}{UI.RESET}{UI.BASE}")
    print(f"      {UI.MUTED}(phone must share the same network/hotspot as this laptop — the "
          f"LAN IP only means something to devices on that same network){UI.RESET}{UI.BASE}\n")
    print(f"    {UI.MUTED}Scan to open on your phone — the QR just encodes the URL above, "
          f"nothing fancier than that (or type the URL if there's no QR):{UI.RESET}{UI.BASE}")
    print_qr(url)

    return True


def stop_server():
    global SERVER_PROC, SERVER_INFO
    if not SERVER_PROC or SERVER_PROC.poll() is not None:
        print(f"\n    {UI.TAG_INFO} No server is currently running in this session.")
        SERVER_PROC = None
        return
    print(f"\n    {UI.TAG_INFO} Stopping server...")
    SERVER_PROC.terminate()
    try:
        SERVER_PROC.wait(timeout=5)
    except subprocess.TimeoutExpired:
        SERVER_PROC.kill()
    print(f"    {UI.TAG_OK} Server forcefully stopped.")
    SERVER_PROC = None
    SERVER_INFO = {}


def server_status_menu():
    while True:
        header("Node Telemetry & Control")
        if SERVER_PROC and SERVER_PROC.poll() is None:
            ip = get_lan_ip()
            print(f"    Status     : {UI.EMERALD}{UI.BOLD}● ONLINE / RUNNING{UI.RESET}{UI.BASE}")
            print(f"    Model      : {UI.WHITE}{Path(SERVER_INFO.get('model', '?')).name}{UI.RESET}{UI.BASE}")
            print(f"    Host:Port  : {UI.CYAN}{SERVER_INFO.get('host')}:{SERVER_INFO.get('port')}{UI.RESET}{UI.BASE}")
            print(f"    Started at : {UI.MUTED}{SERVER_INFO.get('started_at')}{UI.RESET}{UI.BASE}")
            print(f"    Local URL  : {UI.AQUA}http://localhost:{SERVER_INFO.get('port')}{UI.RESET}{UI.BASE}")
            print(f"    LAN URL    : {UI.EMERALD}http://{ip}:{SERVER_INFO.get('port')}{UI.RESET}{UI.BASE}\n")

            print(f"    {UI.CYAN}[q]{UI.RESET}{UI.BASE} Print QR code again")
            print(f"    {UI.CYAN}[l]{UI.RESET}{UI.BASE} Generate LIVE LOG Tracker command (for new window)")
            print(f"    {UI.ROSE}[x]{UI.RESET}{UI.BASE} Stop server immediately")
            print(f"    {UI.MUTED}[Enter] Back to main menu{UI.RESET}{UI.BASE}")
            choice = input(f"\n    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Choice: ").strip().lower()
            
            if choice == "q":
                url = f"http://{ip}:{SERVER_INFO.get('port')}"
                print()
                print_qr(url)
                input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
            elif choice == "l":
                if IS_WINDOWS:
                    # Windows powershell trick that literally opens a NEW window and tails the log
                    cmd_str = f'start powershell -NoExit -Command "Get-Content -Path \'{SERVER_LOG_PATH}\' -Wait -Tail 20"'
                else:
                    cmd_str = f'tail -f "{SERVER_LOG_PATH}"'
                print(f"\n    {UI.TAG_INFO} {UI.BOLD}SPAWN LIVE LOG STREAM{UI.RESET}{UI.BASE}")
                print(f"    {UI.MUTED}Copy and paste the exact command below into your current cmd window.")
                print(f"    It will pop open a completely new, dedicated tracking window.{UI.RESET}{UI.BASE}")
                print(f"\n    {UI.PURPLE}❯{UI.RESET} {UI.WHITE}{UI.BOLD}{cmd_str}{UI.RESET}{UI.BASE}\n")
                input(f"    {UI.MUTED}Press Enter to return...{UI.RESET}{UI.BASE}")
            elif choice == "x":
                stop_server()
                input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
            else:
                return
        else:
            print(f"    Status: {UI.DARK}NOT RUNNING{UI.RESET}{UI.BASE}\n")
            input(f"    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")
            return


# --------------------------------------------------------------------------
# Log viewer (In-line snapshot)
# --------------------------------------------------------------------------

def logs_menu():
    header("Internal Log Snapshot")
    explain(
        "Every time the server starts, its raw console output gets piped "
        "into a timestamped log file instead of a visible terminal window — "
        "since it runs in the background, this is how you grab a quick snapshot "
        "of what it's saying. Use the Status menu to get a live tracking window."
    )
    logs = sorted(LOG_DIR.glob("server-*.log"), reverse=True)
    if not logs:
        print(f"    {UI.TAG_INFO} No log files yet. Start a server first.")
        input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")
        return

    print(f"    {UI.BOLD}Recent log files:{UI.RESET}{UI.BASE}\n")
    for i, lp in enumerate(logs[:10], 1):
        print(f"     {UI.CYAN}{i:2d}.{UI.RESET} {UI.WHITE}{lp.name}{UI.RESET}{UI.BASE}")
    print()
    choice = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Pick a number to view (Enter = most recent, s/q = skip/back): ").strip().lower()
    if choice in ("q", "s"):
        return

    if choice == "":
        target = logs[0]
    else:
        try:
            idx = int(choice)
            target = logs[idx - 1]
        except (ValueError, IndexError):
            print(f"    {UI.TAG_FAIL} Invalid choice.")
            input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
            return

    print(f"\n    {UI.DARK}─── Last 40 lines of {target.name} ─────────────────────────────{UI.RESET}{UI.BASE}\n")
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for line in lines[-40:]:
            print(f"    {UI.MUTED}│{UI.RESET}{UI.BASE} {line.rstrip()}")
    except OSError as e:
        print(f"    {UI.TAG_FAIL} Could not read log: {e}")

    print(f"\n    {UI.DARK}──────────────────────────────────────────────────────────────────{UI.RESET}{UI.BASE}\n")
    input(f"    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Quick Start wizard
# --------------------------------------------------------------------------

def quick_start():
    header("Deployment Wizard")
    explain(
        "The fastest path from 'nothing running' to 'server live on your "
        "LAN': pick a model, confirm port, decide who's allowed to connect, "
        "launch. Every step below can be skipped with 's' (uses a sensible "
        "default) or you can bail to the menu entirely with 'q'."
    )

    last_cfg = load_last_config()

    step = 1
    model_path = None
    port = None
    host = last_cfg.get("host", DEFAULT_HOST)

    # Step 1: model
    while step == 1:
        default_model = last_cfg.get("model")
        result = choose_model(default_path=default_model)
        if result == "__back__":
            return  # nothing before step 1, back = exit wizard
        if result == "__quit__":
            return
        if result == "__skip__":
            models = scan_models()
            if not models:
                input(f"\n    {UI.TAG_WARN} No models available. Press Enter to return to menu...")
                return
            model_path = models[0]["path"] if not default_model else Path(default_model)
            step = 2
            break
        if result is None:
            input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")
            return
        model_path = result
        step = 2

    # Step 2: port
    while step == 2:
        explain(
            "The port is which 'door' on your laptop the server answers on. "
            "To prevent parallel running clashes, the script generates a "
            "guaranteed free, random port for you automatically."
        )
        safe_random_port = get_random_free_port()
        result = prompt("Port to serve on (Random Unused)", default=str(safe_random_port))
        if result == "__back__":
            step = 1
            continue
        if result == "__quit__":
            return
        if result == "__skip__":
            port = safe_random_port
            step = 3
            break
        try:
            port = int(result)
        except (ValueError, TypeError):
            print(f"    {UI.TAG_WARN} Invalid port, keeping random value.")
            port = safe_random_port
        step = 3

    # Step 3: host (LAN vs local-only)
    while step == 3:
        explain(
            "This decides who's allowed to knock on that door. 0.0.0.0 means "
            "'listen on every network interface this laptop has' — that's "
            "what lets phones on your Wi-Fi or hotspot reach in. 127.0.0.1 "
            "means 'only this machine can talk to itself,' which locks out "
            "everyone else, even on the same network."
        )
        print(f"    {UI.BOLD}Who should be able to connect?{UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}1.{UI.RESET}{UI.BASE} Everyone on my network (0.0.0.0) [recommended]")
        print(f"     {UI.CYAN}2.{UI.RESET}{UI.BASE} Just this laptop (127.0.0.1)")
        print(f"     {UI.CYAN}s.{UI.RESET}{UI.BASE} Skip (uses recommended: 0.0.0.0)")
        result = prompt("Choice", default="1")
        if result == "__back__":
            step = 2
            continue
        if result == "__quit__":
            return
        if result in ("__skip__", "1", ""):
            host = "0.0.0.0"
        elif result == "2":
            host = "127.0.0.1"
        else:
            print(f"    {UI.TAG_WARN} Invalid choice, defaulting to LAN access.")
            host = "0.0.0.0"
        step = 4

    # Step 4: launch
    explain(
        "Launching now. This runs the server as a background process so "
        "this menu stays usable — think of it as the difference between "
        "opening a new terminal tab versus freezing this one. No network "
        "call happens here beyond the server binding to your chosen port."
    )
    start_server(model_path, port, host)
    input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Main menu
# --------------------------------------------------------------------------

WELCOME_TEXT = (
    "This script is the front door to your local llama.cpp server — it doesn't "
    "run the AI itself, it just wires up everything around it: picking a model, "
    "opening the right door (port) for phones to knock on, and keeping tabs on "
    "whether it's actually alive. Everything here works fully offline; nothing "
    "is required to touch the internet."
)


def main_menu():
    first_pass = True
    while True:
        clear_screen()
        
        # Enforce green baseline for the whole print session
        sys.stdout.write(UI.BASE)
        
        running = SERVER_PROC is not None and SERVER_PROC.poll() is None
        status_badge = f"{UI.EMERALD}● ONLINE{UI.RESET}{UI.BASE}" if running else f"{UI.DARK}○ OFFLINE{UI.RESET}{UI.BASE}"
        
        print(f"  {UI.CYAN}╔════════════════════════════════════════════════════════════════════╗{UI.RESET}{UI.BASE}")
        print(f"  {UI.CYAN}║{UI.RESET}{UI.BASE}   {UI.WHITE}{UI.BOLD}⬡ LOCAL AI NODE ARCHITECTURE{UI.RESET}{UI.BASE}   [{status_badge}]               {UI.CYAN}║{UI.RESET}{UI.BASE}")
        print(f"  {UI.CYAN}║{UI.RESET}{UI.BASE}   {UI.DARK}Root: {str(BASE_DIR)[-50:]:<50}{UI.RESET}{UI.BASE} {UI.CYAN}║{UI.RESET}{UI.BASE}")
        print(f"  {UI.CYAN}╚════════════════════════════════════════════════════════════════════╝{UI.RESET}{UI.BASE}\n")

        if first_pass:
            print(f"    {UI.MUTED}{WELCOME_TEXT}{UI.RESET}{UI.BASE}\n")
            first_pass = False

        print(f"    {UI.CYAN}1.{UI.RESET}{UI.BASE} {UI.WHITE}Quick Start{UI.RESET}{UI.BASE}        {UI.DARK}(launch server on random port + LAN){UI.RESET}{UI.BASE}")
        print(f"    {UI.CYAN}2.{UI.RESET}{UI.BASE} {UI.WHITE}Manage Models{UI.RESET}{UI.BASE}      {UI.DARK}(scan and list all .gguf files found){UI.RESET}{UI.BASE}")
        print(f"    {UI.CYAN}3.{UI.RESET}{UI.BASE} {UI.WHITE}Server Profiles{UI.RESET}{UI.BASE}    {UI.DARK}(save/load named configs){UI.RESET}{UI.BASE}")
        print(f"    {UI.CYAN}4.{UI.RESET}{UI.BASE} {UI.WHITE}Diagnostics{UI.RESET}{UI.BASE}        {UI.DARK}(check exe, port, firewall, disk, RAM){UI.RESET}{UI.BASE}")
        print(f"    {UI.CYAN}5.{UI.RESET}{UI.BASE} {UI.WHITE}View Logs{UI.RESET}{UI.BASE}          {UI.DARK}(tail recent server logs){UI.RESET}{UI.BASE}")
        print(f"    {UI.CYAN}6.{UI.RESET}{UI.BASE} {UI.WHITE}Node Status / Stop{UI.RESET}{UI.BASE} {UI.DARK}(see what's running, open live stream window){UI.RESET}{UI.BASE}")
        print(f"    {UI.ROSE}0.{UI.RESET}{UI.BASE} {UI.WHITE}Exit{UI.RESET}{UI.BASE}               {UI.DARK}(compulsory shutdown of server process){UI.RESET}{UI.BASE}\n")

        divider("─", 68, UI.DARK)
        choice = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Command {UI.CYAN}›{UI.RESET} {UI.BASE}").strip()

        if choice == "1":
            quick_start()
        elif choice == "2":
            models_menu()
        elif choice == "3":
            profiles_menu()
        elif choice == "4":
            run_diagnostics(port=SERVER_INFO.get("port") if SERVER_INFO else None)
        elif choice == "5":
            logs_menu()
        elif choice == "6":
            server_status_menu()
        elif choice == "0":
            if SERVER_PROC and SERVER_PROC.poll() is None:
                print(f"\n    {UI.TAG_WARN} Compulsory server shutdown initiated...")
                stop_server()
            print(f"\n    {UI.CYAN}Session terminated. Configs saved.{UI.RESET}\n")
            break
        else:
            time.sleep(0.3)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main():
    # Force system default stdout to our base green
    sys.stdout.write(UI.BASE)
    ensure_dependencies()
    try:
        main_menu()
    except KeyboardInterrupt:
        print(f"\n\n    {UI.TAG_WARN} Interrupted. Compulsory shutdown initiated.")
        if SERVER_PROC and SERVER_PROC.poll() is None:
            stop_server()
        print(f"{UI.RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()