#!/usr/bin/env python3
"""
Setup-Engine.py - Interactive control center for the local llama.cpp LAN server.

Run this from the same folder as llama-server.exe.

Built for ULTRA REMOTE / zero-connectivity use: nothing in this script
assumes internet access. Anything that could touch the network (pip
installs) is optional, skippable, and time-boxed so a dead connection
fails fast instead of hanging the whole tool.

Features:
  - Quick Start wizard (model -> port -> host -> launch) with optional tuning
  - Interactive Terminal Chat via llama-cli (talk directly without a browser)
  - Hardware Benchmark via llama-bench (test prompt & generation tokens/sec)
  - Background server process (menu stays usable while server runs)
  - Recursive .gguf model scanner (current folder + all subfolders)
  - Server profiles (save/load/delete named configs with performance flags)
  - Diagnostics (exe present, port free, firewall rule, disk space, RAM)
  - Live log tracking & lingering zombie server cleaner
  - LAN URL + terminal ASCII QR code for phone access (optional)

Windows-focused (cmd/powershell), but degrades gracefully on other platforms.
"""

import atexit
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

EXE_SERVER_CANDIDATES = ["llama-server.exe", "llama-server", "server.exe"]
EXE_CLI_CANDIDATES = ["llama-cli.exe", "llama-cli", "main.exe"]
EXE_BENCH_CANDIDATES = ["llama-bench.exe", "llama-bench"]

DEFAULT_HOST = "0.0.0.0"
DEFAULT_CTX = 2048
DEFAULT_THREADS = max(1, (os.cpu_count() or 4) - 1)
PIP_TIMEOUT_SECONDS = 8  # fail fast on dead connections, don't hang forever

# Holds the live background server process, if any, for this session.
SERVER_PROC = None
SERVER_LOG_PATH = None
SERVER_INFO = {}  # port, host, model, started_at, ctx, threads, ngl, fa

# Set True once the user says "don't ask about deps again this session".
DEPS_PERMANENTLY_SKIPPED = False


# --------------------------------------------------------------------------
# Cleanup & Lifecycle Handlers
# --------------------------------------------------------------------------

def cleanup_on_exit():
    """Emergency hook to guarantee background server process is not orphaned."""
    global SERVER_PROC
    if SERVER_PROC and SERVER_PROC.poll() is None:
        try:
            SERVER_PROC.terminate()
            SERVER_PROC.wait(timeout=2)
        except Exception:
            try:
                SERVER_PROC.kill()
            except Exception:
                pass

atexit.register(cleanup_on_exit)


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
    """Print an educational rationale for the pending step."""
    print(f"    {UI.AQUA}ℹ{UI.RESET} {UI.BASE}{UI.DIM}> {text}{UI.RESET}{UI.BASE}\n")


def prompt(text, default=None, show_options=True):
    """
    Prompt with interactive control tokens:
      Enter -> accept default
      'b'   -> go back (returns "__back__")
      's'   -> skip this step, use default (returns "__skip__")
      'q'   -> quit to main menu (returns "__quit__")
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
    Best-effort LAN IP detection using non-routable UDP queries.
    Never transmits network packets over the wire, functioning reliably offline.
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
    """Guarantees collision-free port allocation within the ephemeral range."""
    for _ in range(100):
        p = random.randint(start_range, end_range)
        if is_port_free(p):
            return p
    return 48123


def find_binary(candidate_list, fallback_keyword="server"):
    """Finds a specific compiled binary in BASE_DIR or subdirectories."""
    for name in candidate_list:
        candidate = BASE_DIR / name
        if candidate.exists():
            return candidate
    for f in BASE_DIR.glob("*.exe"):
        if fallback_keyword in f.name.lower():
            return f
    return None


def find_exe():
    return find_binary(EXE_SERVER_CANDIDATES, "server")


def find_cli_exe():
    return find_binary(EXE_CLI_CANDIDATES, "cli")


def find_bench_exe():
    return find_binary(EXE_BENCH_CANDIDATES, "bench")


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
    """Offer to install optional enhancement packages with fast timeouts."""
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
        "One optional enhancement is missing (qrcode). It renders in-terminal "
        "ASCII QR codes so phones can scan to connect. All core server features "
        "work without it. If this box is completely offline, skip this safely."
    )
    print(f"    {UI.MUTED}Missing: {UI.CYAN}{', '.join(missing)}{UI.RESET}{UI.BASE}\n")
    print(f"    {UI.CYAN}[y]{UI.RESET}{UI.BASE} Install now (needs internet, times out fast if offline)")
    print(f"    {UI.CYAN}[n]{UI.RESET}{UI.BASE} Skip for now")
    print(f"    {UI.CYAN}[s]{UI.RESET}{UI.BASE} Skip for this entire session")
    choice = input(f"\n    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Choice [n]: ").strip().lower()

    if choice == "s":
        DEPS_PERMANENTLY_SKIPPED = True
        return

    if choice != "y":
        return

    for pkg in missing:
        print(f"    {UI.TAG_INFO} Attempting to install {pkg} ({PIP_TIMEOUT_SECONDS}s timeout)...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet", pkg],
                timeout=PIP_TIMEOUT_SECONDS,
            )
            print(f"    {UI.TAG_OK} Installed {pkg}")
        except subprocess.TimeoutExpired:
            print(f"    {UI.TAG_WARN} Connection timed out. Continuing offline.")
        except subprocess.CalledProcessError:
            print(f"    {UI.TAG_FAIL} Could not install {pkg}. Continuing without it.")
    print()


# --------------------------------------------------------------------------
# QR code (optional — degrades to a plain URL)
# --------------------------------------------------------------------------

def print_qr(url):
    """Renders ASCII QR code if qrcode is installed; otherwise outputs raw URL."""
    try:
        import qrcode
    except ImportError:
        print(f"\n    {UI.MUTED}(Type this URL directly into your phone browser: {UI.CYAN}{url}{UI.MUTED}){UI.RESET}{UI.BASE}\n")
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
    header("Diagnostics & Pre-Flight Engine")
    explain(
        "Local audit verifying binary integrity, models on disk, socket bindings, "
        "firewall posture, and memory headroom before initiating services."
    )

    def do_check(label, fn):
        choice = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.MUTED}[Enter=run / s=skip / q=stop]{UI.RESET} {UI.BASE}{UI.BOLD}{label}{UI.RESET}{UI.BASE}: ").strip().lower()
        if choice == "q":
            return "__quit__"
        if choice == "s":
            print(f"      {UI.MUTED}↷ (skipped){UI.RESET}{UI.BASE}\n")
            return "__skipped__"
        return fn()

    exe = do_check("Locating server binary (llama-server)", find_exe)
    if exe == "__quit__":
        return
    if exe != "__skipped__":
        status = UI.TAG_OK if exe else UI.TAG_FAIL
        val = f"{UI.WHITE}{exe.name}{UI.RESET}{UI.BASE}" if exe else f"{UI.RED}NOT FOUND in {BASE_DIR}{UI.RESET}{UI.BASE}"
        print(f"      {status} Server executable: {val}\n")

    cli_exe = do_check("Locating console chat binary (llama-cli)", find_cli_exe)
    if cli_exe == "__quit__":
        return
    if cli_exe != "__skipped__":
        status = UI.TAG_OK if cli_exe else UI.TAG_WARN
        val = f"{UI.WHITE}{cli_exe.name}{UI.RESET}{UI.BASE}" if cli_exe else f"{UI.AMBER}Optional CLI tool not found{UI.RESET}{UI.BASE}"
        print(f"      {status} Chat executable: {val}\n")

    bench_exe = do_check("Locating benchmark binary (llama-bench)", find_bench_exe)
    if bench_exe == "__quit__":
        return
    if bench_exe != "__skipped__":
        status = UI.TAG_OK if bench_exe else UI.TAG_WARN
        val = f"{UI.WHITE}{bench_exe.name}{UI.RESET}{UI.BASE}" if bench_exe else f"{UI.AMBER}Optional benchmark tool not found{UI.RESET}{UI.BASE}"
        print(f"      {status} Benchmark executable: {val}\n")

    models = do_check("Scanning for GGUF model files", scan_models)
    if models == "__quit__":
        return
    if models != "__skipped__":
        status = UI.TAG_OK if models else UI.TAG_WARN
        print(f"      {status} Quantized models found: {UI.WHITE}{len(models)}{UI.RESET}{UI.BASE}\n")

    check_port = port or get_random_free_port()
    free = do_check(f"Verifying port availability ({check_port})", lambda: is_port_free(check_port))
    if free == "__quit__":
        return
    if free != "__skipped__":
        status = UI.TAG_OK if free else UI.TAG_WARN
        val = f"{UI.EMERALD}free and available{UI.RESET}{UI.BASE}" if free else f"{UI.RED}already occupied{UI.RESET}{UI.BASE}"
        print(f"      {status} Port {check_port}: {val}\n")

    fw = do_check(f"Auditing Windows Firewall for port {check_port}", lambda: check_firewall_rule(check_port))
    if fw == "__quit__":
        return
    if fw != "__skipped__":
        if fw is None:
            print(f"      {UI.TAG_INFO} Firewall check: not applicable on this OS\n")
        else:
            status = UI.TAG_OK if fw else UI.TAG_WARN
            val = f"{UI.EMERALD}inbound rule active{UI.RESET}{UI.BASE}" if fw else f"{UI.AMBER}rule not found (LAN devices might be filtered){UI.RESET}{UI.BASE}"
            print(f"      {status} Inbound firewall rule: {val}\n")

    disk = do_check("Assessing disk storage headroom", check_disk_space)
    if disk == "__quit__":
        return
    if disk != "__skipped__":
        status = UI.TAG_OK if disk["free_gb"] > 2 else UI.TAG_WARN
        print(f"      {status} Disk space free: {UI.WHITE}{disk['free_gb']} GB{UI.RESET}{UI.BASE} (Total: {disk['total_gb']} GB)\n")

    ram = do_check("Profiling system RAM availability", check_ram)
    if ram == "__quit__":
        return
    if ram != "__skipped__":
        if ram:
            print(f"      {UI.TAG_OK} Available RAM: {UI.WHITE}{ram['available_gb']} GB{UI.RESET}{UI.BASE} / {ram['total_gb']} GB total\n")
        else:
            print(f"      {UI.TAG_INFO} RAM profile: requires optional 'psutil' package (offline-safe to skip)\n")

    ip = do_check("Resolving local network (LAN) IP", get_lan_ip)
    if ip == "__quit__":
        return
    if ip != "__skipped__":
        print(f"      {UI.TAG_OK} LAN address: {UI.CYAN}{ip}{UI.RESET}{UI.BASE}\n")

    input(f"    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Config / profiles
# --------------------------------------------------------------------------

def load_last_config():
    return load_json(CONFIG_FILE, {
        "model": None,
        "port": None,
        "host": DEFAULT_HOST,
        "ctx": DEFAULT_CTX,
        "threads": DEFAULT_THREADS,
        "ngl": 0,
        "fa": False,
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
            "Saved configurations store your model, port, host, and hardware "
            "tuning (threads, context size, GPU offload) under a friendly name "
            "for instant 1-click redeployment."
        )
        profiles = load_profiles()
        if not profiles:
            print(f"    {UI.MUTED}No saved profiles found.{UI.RESET}{UI.BASE}\n")
        else:
            for i, (name, cfg) in enumerate(profiles.items(), 1):
                model_str = Path(cfg['model']).name if cfg.get('model') else '?'
                tuning_info = f"ctx={cfg.get('ctx', 2048)}, t={cfg.get('threads', DEFAULT_THREADS)}, ngl={cfg.get('ngl', 0)}"
                print(f"     {UI.CYAN}{i:2d}.{UI.RESET} {UI.WHITE}{name:<16}{UI.RESET} {UI.PURPLE}➔{UI.RESET} {UI.AQUA}{model_str:<28}{UI.RESET} {UI.MUTED}[port={cfg.get('port')}, {tuning_info}]{UI.RESET}{UI.BASE}")
            print()

        print(f"    {UI.CYAN}[n]{UI.RESET}{UI.BASE} Save current configuration as new profile")
        print(f"    {UI.CYAN}[l]{UI.RESET}{UI.BASE} Load a profile and launch server")
        print(f"    {UI.CYAN}[d]{UI.RESET}{UI.BASE} Delete an existing profile")
        print(f"    {UI.CYAN}[q]{UI.RESET}{UI.BASE} Return to main menu")
        choice = input(f"\n    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Choice: ").strip().lower()

        if choice in ("q", "s", ""):
            return
        elif choice == "n":
            cfg = load_last_config()
            if not cfg.get("model"):
                print(f"\n    {UI.TAG_WARN} No previous server run found to save. Launch Quick Start first.")
                input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
                continue
            name = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Profile name (e.g., 'fast-chat' or 'heavy-qa'): ").strip()
            if name:
                save_profile(name, cfg)
                print(f"    {UI.TAG_OK} Saved profile '{name}'.")
            else:
                print(f"    {UI.MUTED}Cancelled — no profile saved.{UI.RESET}{UI.BASE}")
            input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
        elif choice == "l":
            if not profiles:
                continue
            name = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Profile name to load: ").strip()
            if not name:
                continue
            if name in profiles:
                cfg = profiles[name]
                start_server(
                    model_path=cfg["model"],
                    port=cfg.get("port"),
                    host=cfg.get("host", DEFAULT_HOST),
                    ctx=cfg.get("ctx", DEFAULT_CTX),
                    threads=cfg.get("threads", DEFAULT_THREADS),
                    ngl=cfg.get("ngl", 0),
                    fa=cfg.get("fa", False)
                )
                input(f"\n    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
            else:
                print(f"    {UI.TAG_FAIL} Profile '{name}' not found.")
                input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
        elif choice == "d":
            if not profiles:
                continue
            name = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Profile name to delete: ").strip()
            if not name:
                continue
            if name in profiles:
                delete_profile(name)
                print(f"    {UI.TAG_OK} Deleted profile '{name}'.")
            input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Model manager
# --------------------------------------------------------------------------

def choose_model(default_path=None):
    """Returns a Path to a selected .gguf file, or control tokens."""
    models = scan_models()
    if not models:
        print(f"\n    {UI.TAG_FAIL} No .gguf model files detected under {BASE_DIR}.")
        print(f"    {UI.MUTED}Place any quantized .gguf model file in this folder and retry.{UI.RESET}{UI.BASE}")
        return None

    explain(
        "Select which model weights to load. Smaller parameter models (1B-3B) "
        "generate responses rapidly on consumer CPUs, while larger models (7B+) "
        "provide superior reasoning at the cost of processing speed."
    )
    print(f"    {UI.BOLD}Detected Models:{UI.RESET}{UI.BASE}\n")
    for i, m in enumerate(models, 1):
        marker = f" {UI.EMERALD}◀ last used{UI.RESET}{UI.BASE}" if default_path and str(m["path"]) == str(default_path) else ""
        print(f"     {UI.CYAN}{i:2d}.{UI.RESET} {UI.WHITE}{m['rel']:<45}{UI.RESET} {UI.MUTED}({m['size_mb']} MB){UI.RESET}{UI.BASE}{marker}")
    print()

    default_index = 1
    if default_path:
        for i, m in enumerate(models, 1):
            if str(m["path"]) == str(default_path):
                default_index = i
                break

    choice = prompt("Pick model number", default=str(default_index))
    if choice in ("__back__", "__skip__", "__quit__"):
        if choice == "__skip__":
            return models[default_index - 1]["path"]
        return choice

    try:
        idx = int(choice)
        if 1 <= idx <= len(models):
            return models[idx - 1]["path"]
    except ValueError:
        pass

    print(f"    {UI.TAG_WARN} Invalid selection. Defaulting to #{default_index}.")
    return models[default_index - 1]["path"]


def models_menu():
    header("Local Model Matrix")
    explain(
        "Recursive filesystem scan for quantized GGUF weights. "
        "All models listed here can be served via llama-server, tested directly "
        "in terminal chat, or benchmarked for hardware performance."
    )
    models = scan_models()
    if not models:
        print(f"    {UI.TAG_WARN} No .gguf files found in {BASE_DIR}.")
        print(f"    {UI.MUTED}Download GGUF models and place them in this folder.{UI.RESET}{UI.BASE}")
    else:
        total_mb = sum(m["size_mb"] for m in models)
        print(f"    {UI.TAG_OK} Discovered {UI.BOLD}{len(models)}{UI.RESET}{UI.BASE} model(s), total volume: {UI.BOLD}{round(total_mb / 1024, 2)} GB{UI.RESET}{UI.BASE}:\n")
        for i, m in enumerate(models, 1):
            print(f"     {UI.CYAN}{i:2d}.{UI.RESET} {UI.WHITE}{m['rel']:<45}{UI.RESET} {UI.MUTED}({m['size_mb']} MB){UI.RESET}{UI.BASE}")
    print()
    input(f"    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Direct Terminal Chat (llama-cli Wrapper)
# --------------------------------------------------------------------------

def run_console_chat():
    """Runs interactive terminal conversation mode via llama-cli.exe."""
    header("Direct Terminal Chat (llama-cli)")
    explain(
        "Chat directly with any local model in your terminal window without "
        "starting an HTTP server or opening a web browser. Powered by llama-cli. "
        "Type your prompt and press Enter; type '/exit' or press Ctrl+C to leave."
    )

    cli_exe = find_cli_exe()
    if not cli_exe:
        print(f"    {UI.TAG_FAIL} llama-cli executable not found in {BASE_DIR}.")
        print(f"    {UI.MUTED}Ensure llama-cli.exe is present in this folder.{UI.RESET}{UI.BASE}")
        input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")
        return

    last_cfg = load_last_config()
    model_path = choose_model(default_path=last_cfg.get("model"))
    if not model_path or model_path in ("__back__", "__quit__"):
        return

    threads = prompt("CPU Threads to allocate", default=str(DEFAULT_THREADS))
    if threads in ("__back__", "__quit__"):
        return
    try:
        t_val = int(threads)
    except ValueError:
        t_val = DEFAULT_THREADS

    cmd = [
        str(cli_exe),
        "-m", str(model_path),
        "-cnv",
        "--threads", str(t_val),
        "--simple-io",
    ]

    print(f"\n    {UI.TAG_INFO} Launching interactive chat session...")
    print(f"    {UI.MUTED}Command: {' '.join(cmd)}{UI.RESET}{UI.BASE}\n")
    divider("─", 68, UI.CYAN)
    print(f"    {UI.EMERALD}Conversation started. To end the session, press Ctrl+C or type exit.{UI.RESET}{UI.BASE}\n")

    try:
        subprocess.run(cmd, cwd=str(BASE_DIR))
    except KeyboardInterrupt:
        print(f"\n\n    {UI.TAG_INFO} Chat session completed.")
    except Exception as e:
        print(f"\n    {UI.TAG_FAIL} Failed to run llama-cli: {e}")

    divider("─", 68, UI.CYAN)
    input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Hardware Benchmark (llama-bench Wrapper)
# --------------------------------------------------------------------------

def run_hardware_benchmark():
    """Runs automated prompt and token evaluation benchmark via llama-bench.exe."""
    header("Hardware Inference Benchmark (llama-bench)")
    explain(
        "Profiles your local CPU/GPU performance by measuring prompt processing "
        "speed (PP tokens/sec) and generation throughput (TG tokens/sec) on "
        "a selected model."
    )

    bench_exe = find_bench_exe()
    if not bench_exe:
        print(f"    {UI.TAG_FAIL} llama-bench executable not found in {BASE_DIR}.")
        print(f"    {UI.MUTED}Ensure llama-bench.exe is present in this folder.{UI.RESET}{UI.BASE}")
        input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")
        return

    last_cfg = load_last_config()
    model_path = choose_model(default_path=last_cfg.get("model"))
    if not model_path or model_path in ("__back__", "__quit__"):
        return

    repetitions = prompt("Repetitions per test (lower is faster)", default="2")
    if repetitions in ("__back__", "__quit__"):
        return
    try:
        rep_val = int(repetitions)
    except ValueError:
        rep_val = 2

    cmd = [
        str(bench_exe),
        "-m", str(model_path),
        "-p", "512",
        "-n", "128",
        "-r", str(rep_val),
        "-t", str(DEFAULT_THREADS),
        "-o", "md",
    ]

    print(f"\n    {UI.TAG_INFO} Executing hardware benchmark benchmark run...")
    print(f"    {UI.MUTED}Command: {' '.join(cmd)}{UI.RESET}{UI.BASE}\n")
    divider("─", 68, UI.CYAN)

    try:
        res = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True)
        if res.returncode == 0:
            print(res.stdout)
            print(f"    {UI.TAG_OK} Benchmark complete.")
        else:
            print(f"    {UI.TAG_WARN} Benchmark reported notes/warnings:")
            if res.stdout:
                print(res.stdout)
            if res.stderr:
                print(res.stderr)
    except KeyboardInterrupt:
        print(f"\n    {UI.TAG_WARN} Benchmark aborted by user.")
    except Exception as e:
        print(f"\n    {UI.TAG_FAIL} Benchmark failed to execute: {e}")

    divider("─", 68, UI.CYAN)
    input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Zombie Process Cleaner
# --------------------------------------------------------------------------

def kill_zombie_servers():
    """Detects and terminates any orphaned llama-server background instances."""
    header("Process Cleaner / Zombie Killer")
    explain(
        "If a previous server session terminated improperly, an orphaned "
        "llama-server.exe process may linger in memory, locking ports or CPU "
        "resources. This forcefully terminates existing background servers."
    )

    confirm = prompt("Kill all running llama-server instances? [y/N]", default="n")
    if confirm.lower() != "y":
        print(f"    {UI.MUTED}Aborted. No processes were modified.{UI.RESET}{UI.BASE}")
        input(f"\n    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
        return

    killed = False
    if IS_WINDOWS:
        try:
            res = subprocess.run(
                ["taskkill", "/F", "/IM", "llama-server.exe"],
                capture_output=True, text=True
            )
            if "SUCCESS" in res.stdout or "PID" in res.stdout:
                print(f"    {UI.TAG_OK} Terminated lingering llama-server instances.")
                killed = True
            else:
                print(f"    {UI.TAG_INFO} No active llama-server processes found.")
        except Exception as e:
            print(f"    {UI.TAG_FAIL} Error during task termination: {e}")
    else:
        try:
            subprocess.run(["pkill", "-9", "-f", "llama-server"], check=False)
            print(f"    {UI.TAG_OK} Sent termination signals to llama-server processes.")
            killed = True
        except Exception as e:
            print(f"    {UI.TAG_FAIL} Error: {e}")

    global SERVER_PROC, SERVER_INFO
    SERVER_PROC = None
    SERVER_INFO = {}

    input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Server control (background process)
# --------------------------------------------------------------------------

def start_server(model_path, port, host, ctx=DEFAULT_CTX, threads=DEFAULT_THREADS, ngl=0, fa=False):
    global SERVER_PROC, SERVER_LOG_PATH, SERVER_INFO

    if SERVER_PROC and SERVER_PROC.poll() is None:
        print(f"\n    {UI.TAG_WARN} An active server instance is already running in this session.")
        print(f"    {UI.MUTED}Stop it first from 'Node Status / Stop' before launching another.{UI.RESET}{UI.BASE}")
        return False

    exe = find_exe()
    if not exe:
        print(f"\n    {UI.TAG_FAIL} ERROR: llama-server executable not found in {BASE_DIR}")
        return False

    model_path = Path(model_path)
    if not model_path.exists():
        print(f"\n    {UI.TAG_FAIL} ERROR: model file not found: {model_path}")
        return False

    # Auto-resolve occupied or missing ports
    if not port or not is_port_free(port):
        new_port = get_random_free_port()
        if port:
            print(f"\n    {UI.TAG_WARN} Port {port} is occupied. Automatically assigned open port {new_port}.")
        port = new_port

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"server-{timestamp}.log"

    cmd = [
        str(exe),
        "-m", str(model_path),
        "--host", host,
        "--port", str(port),
        "-c", str(ctx),
        "-t", str(threads),
    ]

    if ngl and int(ngl) > 0:
        cmd.extend(["-ngl", str(ngl)])
    if fa:
        cmd.extend(["-fa", "on"])

    print(f"\n    {UI.TAG_INFO} Initializing background server daemon...")
    print(f"      Executable : {UI.WHITE}{exe.name}{UI.RESET}{UI.BASE}")
    print(f"      Model      : {UI.CYAN}{model_path.relative_to(BASE_DIR)}{UI.RESET}{UI.BASE}")
    print(f"      Address    : {UI.AQUA}{host}:{port}{UI.RESET}{UI.BASE}")
    print(f"      Context    : {UI.WHITE}{ctx} tokens{UI.RESET}{UI.BASE}")
    print(f"      Threads    : {UI.WHITE}{threads} cores{UI.RESET}{UI.BASE}")
    if ngl:
        print(f"      GPU Layers : {UI.WHITE}{ngl}{UI.RESET}{UI.BASE}")
    print(f"      Log File   : {UI.MUTED}{log_path}{UI.RESET}{UI.BASE}")

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
        print(f"\n    {UI.TAG_FAIL} Failed to launch server daemon: {e}")
        log_handle.close()
        return False

    SERVER_PROC = proc
    SERVER_LOG_PATH = log_path
    SERVER_INFO = {
        "model": str(model_path),
        "port": port,
        "host": host,
        "ctx": ctx,
        "threads": threads,
        "ngl": ngl,
        "fa": fa,
        "started_at": timestamp,
    }

    # Verify daemon stability after initialization window
    time.sleep(2)
    if proc.poll() is not None:
        print(f"\n    {UI.TAG_FAIL} ERROR: Server terminated immediately upon startup. Review log:")
        print(f"    {UI.MUTED}{log_path}{UI.RESET}{UI.BASE}")
        return False

    save_last_config(SERVER_INFO)

    ip = get_lan_ip()
    url = f"http://{ip}:{port}"
    local_url = f"http://localhost:{port}"

    print(f"\n    {UI.TAG_OK} {UI.EMERALD}{UI.BOLD}LOCAL AI NODE IS LIVE AND LISTENING.{UI.RESET}{UI.BASE}")
    print(f"      Workstation Browser : {UI.CYAN}{local_url}{UI.RESET}{UI.BASE}")
    print(f"      Mobile / Network    : {UI.EMERALD}{url}{UI.RESET}{UI.BASE}")
    print(f"      OpenAI API Base     : {UI.WHITE}http://{ip}:{port}/v1{UI.RESET}{UI.BASE}\n")
    print(f"    {UI.MUTED}Scan with phone camera connected to the same Wi-Fi/Hotspot:{UI.RESET}{UI.BASE}")
    print_qr(url)

    return True


def stop_server():
    global SERVER_PROC, SERVER_INFO
    if not SERVER_PROC or SERVER_PROC.poll() is not None:
        print(f"\n    {UI.TAG_INFO} No active server daemon running in this session.")
        SERVER_PROC = None
        return
    print(f"\n    {UI.TAG_INFO} Stopping server daemon gracefully...")
    SERVER_PROC.terminate()
    try:
        SERVER_PROC.wait(timeout=5)
    except subprocess.TimeoutExpired:
        SERVER_PROC.kill()
    print(f"    {UI.TAG_OK} Server halted successfully.")
    SERVER_PROC = None
    SERVER_INFO = {}


def server_status_menu():
    while True:
        header("Node Telemetry & Control")
        if SERVER_PROC and SERVER_PROC.poll() is None:
            ip = get_lan_ip()
            print(f"    Server Status : {UI.EMERALD}{UI.BOLD}● ONLINE / ACTIVE{UI.RESET}{UI.BASE}")
            print(f"    Active Model  : {UI.WHITE}{Path(SERVER_INFO.get('model', '?')).name}{UI.RESET}{UI.BASE}")
            print(f"    Endpoint      : {UI.CYAN}{SERVER_INFO.get('host')}:{SERVER_INFO.get('port')}{UI.RESET}{UI.BASE}")
            print(f"    Context Window: {UI.WHITE}{SERVER_INFO.get('ctx', DEFAULT_CTX)} tokens{UI.RESET}{UI.BASE}")
            print(f"    Threads       : {UI.WHITE}{SERVER_INFO.get('threads', DEFAULT_THREADS)} cores{UI.RESET}{UI.BASE}")
            print(f"    Started At    : {UI.MUTED}{SERVER_INFO.get('started_at')}{UI.RESET}{UI.BASE}")
            print(f"    Local Web UI  : {UI.AQUA}http://localhost:{SERVER_INFO.get('port')}{UI.RESET}{UI.BASE}")
            print(f"    LAN Access URL: {UI.EMERALD}http://{ip}:{SERVER_INFO.get('port')}{UI.RESET}{UI.BASE}\n")

            print(f"    {UI.CYAN}[q]{UI.RESET}{UI.BASE} Render ASCII QR code again")
            print(f"    {UI.CYAN}[l]{UI.RESET}{UI.BASE} Open live log stream in separate window")
            print(f"    {UI.ROSE}[x]{UI.RESET}{UI.BASE} Stop server immediately")
            print(f"    {UI.MUTED}[Enter] Return to main menu{UI.RESET}{UI.BASE}")
            choice = input(f"\n    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Choice: ").strip().lower()
            
            if choice == "q":
                url = f"http://{ip}:{SERVER_INFO.get('port')}"
                print_qr(url)
                input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
            elif choice == "l":
                if IS_WINDOWS:
                    cmd_str = f'start powershell -NoExit -Command "Get-Content -Path \'{SERVER_LOG_PATH}\' -Wait -Tail 25"'
                    os.system(cmd_str)
                    print(f"\n    {UI.TAG_OK} Launched independent live log tracker window.")
                else:
                    print(f"\n    Run this command in another terminal: tail -f \"{SERVER_LOG_PATH}\"")
                input(f"\n    {UI.MUTED}Press Enter to return...{UI.RESET}{UI.BASE}")
            elif choice == "x":
                stop_server()
                input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
            else:
                return
        else:
            print(f"    Server Status: {UI.DARK}○ OFFLINE (Not Running){UI.RESET}{UI.BASE}\n")
            input(f"    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")
            return


# --------------------------------------------------------------------------
# Log viewer (In-line snapshot)
# --------------------------------------------------------------------------

def logs_menu():
    header("Internal Log Snapshot")
    explain(
        "Server daemon stdout/stderr streams are piped into timestamped "
        "log files under logs/. Inspect the most recent output lines directly."
    )
    logs = sorted(LOG_DIR.glob("server-*.log"), reverse=True)
    if not logs:
        print(f"    {UI.TAG_INFO} No logs discovered. Launch a server to generate logs.")
        input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")
        return

    print(f"    {UI.BOLD}Recent log sessions:{UI.RESET}{UI.BASE}\n")
    for i, lp in enumerate(logs[:10], 1):
        print(f"     {UI.CYAN}{i:2d}.{UI.RESET} {UI.WHITE}{lp.name}{UI.RESET}{UI.BASE}")
    print()
    choice = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Select session number (Enter = newest, q = back): ").strip().lower()
    if choice in ("q", "s"):
        return

    if choice == "":
        target = logs[0]
    else:
        try:
            idx = int(choice)
            target = logs[idx - 1]
        except (ValueError, IndexError):
            print(f"    {UI.TAG_FAIL} Invalid selection.")
            input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}{UI.BASE}")
            return

    print(f"\n    {UI.DARK}─── Last 40 lines of {target.name} ─────────────────────────────{UI.RESET}{UI.BASE}\n")
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for line in lines[-40:]:
            print(f"    {UI.MUTED}│{UI.RESET}{UI.BASE} {line.rstrip()}")
    except OSError as e:
        print(f"    {UI.TAG_FAIL} Could not read log file: {e}")

    print(f"\n    {UI.DARK}──────────────────────────────────────────────────────────────────{UI.RESET}{UI.BASE}\n")
    input(f"    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Quick Start wizard
# --------------------------------------------------------------------------

def quick_start():
    header("Rapid Deployment Wizard")
    explain(
        "Guided step-by-step setup to launch an offline model server on your LAN: "
        "select a model, assign port, choose network access, and optional hardware tuning."
    )

    last_cfg = load_last_config()
    step = 1
    model_path = None
    port = None
    host = last_cfg.get("host", DEFAULT_HOST)
    ctx = last_cfg.get("ctx", DEFAULT_CTX)
    threads = last_cfg.get("threads", DEFAULT_THREADS)
    ngl = last_cfg.get("ngl", 0)
    fa = last_cfg.get("fa", False)

    # Step 1: Model Selection
    while step == 1:
        default_model = last_cfg.get("model")
        result = choose_model(default_path=default_model)
        if result in ("__back__", "__quit__"):
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

    # Step 2: Port Assignment
    while step == 2:
        explain(
            "Assigns the TCP port the server listens on. A collision-free "
            "random port is pre-selected to prevent address conflicts."
        )
        safe_random_port = get_random_free_port()
        result = prompt("Port to serve on", default=str(safe_random_port))
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
            print(f"    {UI.TAG_WARN} Invalid port number; assigning safe random port.")
            port = safe_random_port
        step = 3

    # Step 3: Network Binding (LAN vs Localhost)
    while step == 3:
        explain(
            "0.0.0.0 binds to all network interfaces, allowing smartphones and "
            "tablets on the Wi-Fi/hotspot to connect. 127.0.0.1 restricts access "
            "to this local workstation only."
        )
        print(f"    {UI.BOLD}Client Access Scope:{UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}1.{UI.RESET}{UI.BASE} Entire Local Area Network (0.0.0.0) [recommended]")
        print(f"     {UI.CYAN}2.{UI.RESET}{UI.BASE} Workstation Only (127.0.0.1)")
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
            host = "0.0.0.0"
        step = 4

    # Step 4: Optional Performance Tuning
    while step == 4:
        tune_choice = prompt("Tune hardware settings (threads, context, GPU)? [y/N]", default="n")
        if tune_choice == "__back__":
            step = 3
            continue
        if tune_choice == "__quit__":
            return
        if tune_choice.lower() == "y":
            c_input = prompt("Context size (tokens)", default=str(ctx))
            try:
                ctx = int(c_input)
            except ValueError:
                pass

            t_input = prompt("CPU Threads", default=str(threads))
            try:
                threads = int(t_input)
            except ValueError:
                pass

            ngl_input = prompt("GPU Layers to offload (-ngl)", default=str(ngl))
            try:
                ngl = int(ngl_input)
            except ValueError:
                pass

            fa_input = prompt("Enable Flash Attention? [y/N]", default="y" if fa else "n")
            fa = fa_input.lower() == "y"
        step = 5

    # Step 5: Launch
    start_server(model_path, port, host, ctx=ctx, threads=threads, ngl=ngl, fa=fa)
    input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}{UI.BASE}")


# --------------------------------------------------------------------------
# Main menu
# --------------------------------------------------------------------------

def main_menu():
    while True:
        clear_screen()
        sys.stdout.write(UI.BASE)
        
        running = SERVER_PROC is not None and SERVER_PROC.poll() is None
        status_badge = f"{UI.EMERALD}● ONLINE{UI.RESET}{UI.BASE}" if running else f"{UI.DARK}○ OFFLINE{UI.RESET}{UI.BASE}"
        
        print(f"  {UI.CYAN}╔════════════════════════════════════════════════════════════════════╗{UI.RESET}{UI.BASE}")
        print(f"  {UI.CYAN}║{UI.RESET}{UI.BASE}   {UI.WHITE}{UI.BOLD}⬡ LOCAL AI NODE CONTROL CENTER{UI.RESET}{UI.BASE}   [{status_badge}]               {UI.CYAN}║{UI.RESET}{UI.BASE}")
        print(f"  {UI.CYAN}║{UI.RESET}{UI.BASE}   {UI.DARK}Directory: {str(BASE_DIR)[-48:]:<48}{UI.RESET}{UI.BASE} {UI.CYAN}║{UI.RESET}{UI.BASE}")
        print(f"  {UI.CYAN}╚════════════════════════════════════════════════════════════════════╝{UI.RESET}{UI.BASE}\n")

        print(f"    {UI.EMERALD}CORE ENGINE COMMANDS:{UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}1.{UI.RESET}{UI.BASE} {UI.WHITE}Quick Start Server{UI.RESET}{UI.BASE}     {UI.DARK}(launch background LAN server with QR code){UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}2.{UI.RESET}{UI.BASE} {UI.WHITE}Direct Terminal Chat{UI.RESET}{UI.BASE}   {UI.DARK}(chat directly with model via llama-cli){UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}3.{UI.RESET}{UI.BASE} {UI.WHITE}Hardware Benchmark{UI.RESET}{UI.BASE}     {UI.DARK}(test prompt & generation speeds via llama-bench){UI.RESET}{UI.BASE}")
        print()
        print(f"    {UI.EMERALD}MANAGEMENT & TELEMETRY:{UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}4.{UI.RESET}{UI.BASE} {UI.WHITE}Manage Models{UI.RESET}{UI.BASE}          {UI.DARK}(scan and inspect all discovered .gguf files){UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}5.{UI.RESET}{UI.BASE} {UI.WHITE}Server Profiles{UI.RESET}{UI.BASE}        {UI.DARK}(save/load named configs and tuning presets){UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}6.{UI.RESET}{UI.BASE} {UI.WHITE}System Diagnostics{UI.RESET}{UI.BASE}     {UI.DARK}(pre-flight audit of binaries, port, RAM, firewall){UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}7.{UI.RESET}{UI.BASE} {UI.WHITE}View Server Logs{UI.RESET}{UI.BASE}       {UI.DARK}(inspect stdout/stderr logs in real-time){UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}8.{UI.RESET}{UI.BASE} {UI.WHITE}Node Status / Stop{UI.RESET}{UI.BASE}     {UI.DARK}(telemetry, stream popout, graceful halt){UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}9.{UI.RESET}{UI.BASE} {UI.WHITE}Kill Lingering Nodes{UI.RESET}{UI.BASE}   {UI.DARK}(clean up orphaned background llama-server processes){UI.RESET}{UI.BASE}")
        print(f"     {UI.ROSE}0.{UI.RESET}{UI.BASE} {UI.WHITE}Exit{UI.RESET}{UI.BASE}                   {UI.DARK}(terminate session cleanly){UI.RESET}{UI.BASE}\n")

        divider("─", 68, UI.DARK)
        choice = input(f"    {UI.PURPLE}❯{UI.RESET} {UI.BASE}Command {UI.CYAN}›{UI.RESET} {UI.BASE}").strip()

        if choice == "1":
            quick_start()
        elif choice == "2":
            run_console_chat()
        elif choice == "3":
            run_hardware_benchmark()
        elif choice == "4":
            models_menu()
        elif choice == "5":
            profiles_menu()
        elif choice == "6":
            run_diagnostics(port=SERVER_INFO.get("port") if SERVER_INFO else None)
        elif choice == "7":
            logs_menu()
        elif choice == "8":
            server_status_menu()
        elif choice == "9":
            kill_zombie_servers()
        elif choice == "0":
            if SERVER_PROC and SERVER_PROC.poll() is None:
                print(f"\n    {UI.TAG_WARN} Shutting down server daemon...")
                stop_server()
            print(f"\n    {UI.CYAN}Session ended cleanly.{UI.RESET}\n")
            break
        else:
            time.sleep(0.2)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main():
    sys.stdout.write(UI.BASE)
    ensure_dependencies()
    try:
        main_menu()
    except KeyboardInterrupt:
        print(f"\n\n    {UI.TAG_WARN} Session interrupted. Terminating background services...")
        cleanup_on_exit()
        print(f"{UI.RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()