#!/usr/bin/env python3
"""
Setup-Engine.py - Next-Gen Interactive Control Center for llama.cpp LAN Node.

Interactive terminal control center designed for ULTRA REMOTE / air-gapped environments.
Provides single-stroke keyboard navigation, hardware diagnostics, multi-tool wrappers
(llama-server, llama-cli, llama-bench), closed-loop HTTP health telemetry, and OpenAI-compatible
in-terminal test queries.

Built on Python standard library modules (socket, subprocess, ctypes, urllib, msvcrt).
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
import urllib.error
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------
# Terminal Architecture & ANSI Modernization
# --------------------------------------------------------------------------

IS_WINDOWS = platform.system() == "Windows"

# Platform-specific instant keypress reader
if IS_WINDOWS:
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
else:
    msvcrt = None


def init_terminal():
    """Enables native Virtual Terminal / ANSI escape processing in Windows cmd/pwsh."""
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
    
    # Futuristic Neo-Console Palette
    BASE    = "\033[38;5;48m"   # Bright Terminal Green
    CYAN    = "\033[38;5;51m"   # Accent Cyan
    AQUA    = "\033[38;5;45m"   # Soft Aqua
    PURPLE  = "\033[38;5;141m"  # Interactive Prompts
    VIOLET  = "\033[38;5;99m"
    EMERALD = "\033[38;5;46m"
    AMBER   = "\033[38;5;214m"  # Warnings / Cautions
    ROSE    = "\033[38;5;204m"
    RED     = "\033[38;5;196m"  # Errors / Terminations
    WHITE   = "\033[38;5;255m"  # Primary Text / Headers
    MUTED   = "\033[38;5;244m"  # Subdued Borders
    DARK    = "\033[38;5;238m"  # Background framing

    # Status Badges
    TAG_OK   = f"\033[38;5;46m[ ✔ OK ]{RESET}{BASE}"
    TAG_WARN = f"\033[38;5;214m[ ▲ WARN ]{RESET}{BASE}"
    TAG_FAIL = f"\033[38;5;196m[ ✖ FAIL ]{RESET}{BASE}"
    TAG_INFO = f"\033[38;5;51m[ ◈ INFO ]{RESET}{BASE}"


# --------------------------------------------------------------------------
# Paths & Default Hardware Parameters
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
DEFAULT_PARALLEL = 1
PIP_TIMEOUT_SECONDS = 8
MAX_LOG_RETENTION = 10

# Live background process tracking
SERVER_PROC = None
SERVER_LOG_PATH = None
SERVER_INFO = {}

DEPS_PERMANENTLY_SKIPPED = False


# --------------------------------------------------------------------------
# Defensive Process Hygiene & Clean Shutdown
# --------------------------------------------------------------------------

def cleanup_on_exit():
    """Compulsory termination hook registered with atexit and Win32 console handler."""
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

# Hook Windows Console Close Event (Window [X] Click / CTRL_CLOSE_EVENT)
if IS_WINDOWS:
    try:
        PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
        def win32_ctrl_handler(dwCtrlType):
            cleanup_on_exit()
            return False  # Let default OS handler complete the shutdown
        _win32_handler = PHANDLER_ROUTINE(win32_ctrl_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_win32_handler, True)
    except Exception:
        pass


def prune_logs(max_retention=MAX_LOG_RETENTION):
    """Automatically prunes old log files to prevent infinite disk sprawl."""
    try:
        logs = sorted(LOG_DIR.glob("server-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old_log in logs[max_retention:]:
            try:
                old_log.unlink()
            except OSError:
                pass
    except Exception:
        pass


# --------------------------------------------------------------------------
# Single-Stroke Interactive Input System
# --------------------------------------------------------------------------

def read_single_key(prompt_text="", valid_keys=None):
    """
    Captures a single keypress without requiring the Enter key on Windows.
    Falls back gracefully to standard line input on other environments.
    """
    sys.stdout.write(f"    {UI.PURPLE}❯{UI.RESET} {UI.BASE}{prompt_text}")
    sys.stdout.flush()

    if msvcrt and sys.stdin.isatty():
        while True:
            char = msvcrt.getch()
            # Handle special or arrow key prefixes
            if char in (b'\x00', b'\xe0'):
                msvcrt.getch()  # consume second code
                continue
            try:
                k = char.decode('utf-8', errors='ignore')
            except Exception:
                continue

            if valid_keys is None or k.lower() in [v.lower() for v in valid_keys] or k in ('\r', '\n', '\x1b'):
                # Handle Enter / Esc
                if k in ('\r', '\n'):
                    sys.stdout.write("\n")
                    return ""
                if k == '\x1b':
                    sys.stdout.write("Esc\n")
                    return "__back__"
                sys.stdout.write(f"{k}\n")
                return k.lower()
    else:
        try:
            line = input().strip().lower()
            return line[:1] if line else ""
        except (KeyboardInterrupt, EOFError):
            return "__quit__"


def clear_screen():
    os.system("cls" if IS_WINDOWS else "clear")


def draw_box(title, subtitle=None, width=68):
    """Draws a modern rounded-corner card header."""
    clear_screen()
    top = f"  {UI.CYAN}╭─ {UI.WHITE}{UI.BOLD}{title}{UI.RESET}{UI.CYAN} " + "─" * max(0, width - len(title) - 5) + f"╮{UI.RESET}{UI.BASE}"
    print(top)
    if subtitle:
        print(f"  {UI.CYAN}│{UI.RESET}{UI.BASE}  {UI.MUTED}{subtitle:<{width - 4}}{UI.RESET}{UI.CYAN}│{UI.RESET}{UI.BASE}")
    bot = f"  {UI.CYAN}╰" + "─" * (width - 2) + f"╯{UI.RESET}{UI.BASE}\n"
    print(bot)


def breadcrumb(*crumbs):
    path_str = f" {UI.MUTED}›{UI.RESET} ".join([f"{UI.CYAN}{c}{UI.RESET}" for c in crumbs])
    print(f"  {UI.MUTED}Location:{UI.RESET} {path_str}\n")


def explain(text):
    print(f"    {UI.AQUA}ℹ{UI.RESET} {UI.BASE}{UI.DIM}{text}{UI.RESET}{UI.BASE}\n")


def prompt(text, default=None, show_options=True):
    """Full text input prompt for custom entries with breadcrumb shortcuts."""
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


# --------------------------------------------------------------------------
# Network, Port & Health Telemetry
# --------------------------------------------------------------------------

def get_lan_ip():
    """Resolves the active LAN IP interface via local socket routing without WAN transmission."""
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
    s.settimeout(0.5)
    try:
        result = s.connect_ex((host if host != "0.0.0.0" else "127.0.0.1", port))
        return result != 0
    finally:
        s.close()


def get_random_free_port(start_range=10000, end_range=60000):
    for _ in range(100):
        p = random.randint(start_range, end_range)
        if is_port_free(p):
            return p
    return 48123


def probe_server_health(port, timeout_s=0.3):
    """Queries llama-server's native /health endpoint to gauge latency and slot status."""
    try:
        t0 = time.perf_counter()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/health",
            headers={"User-Agent": "Local-AI-Node"}
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            body = json.loads(resp.read().decode('utf-8'))
            return {"status": "ok", "latency_ms": latency_ms, "body": body}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def wait_for_server_ready(proc, port, timeout_s=25):
    """
    Closed-loop readiness check: Polls /health with an animated spinner
    until the server is ready to process tokens or crashes.
    """
    spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    start = time.perf_counter()
    i = 0
    sys.stdout.write(f"\n    {UI.CYAN}◈ Initializing weights & socket binding...{UI.RESET}\n")

    while time.perf_counter() - start < timeout_s:
        if proc.poll() is not None:
            return False, "Server process terminated unexpectedly during initialization."

        h = probe_server_health(port, timeout_s=0.4)
        if h.get("status") == "ok":
            elapsed = round(time.perf_counter() - start, 1)
            sys.stdout.write(f"\r    {UI.TAG_OK} Model loaded and HTTP socket bound in {elapsed}s.    \n")
            sys.stdout.flush()
            return True, None

        spin = spinner[i % len(spinner)]
        elapsed = round(time.perf_counter() - start, 1)
        sys.stdout.write(f"\r    {UI.AMBER}{spin}{UI.RESET} {UI.MUTED}Loading weights into memory ({elapsed}s)...{UI.RESET}")
        sys.stdout.flush()
        i += 1
        time.sleep(0.3)

    sys.stdout.write("\n")
    return False, f"Server did not respond on /health within {timeout_s} seconds."


def query_chat_test(port, user_message="Hello, test connection."):
    """Performs an in-terminal test chat completion against /v1/chat/completions."""
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    payload = {
        "messages": [
            {"role": "system", "content": "You are a concise test assistant. Respond in one sentence."},
            {"role": "user", "content": user_message}
        ],
        "max_tokens": 64,
        "temperature": 0.7
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json", "User-Agent": "Local-AI-Node"}
        )
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            elapsed = round(time.perf_counter() - t0, 2)
            res_data = json.loads(resp.read().decode('utf-8'))
            reply = res_data["choices"][0]["message"]["content"].strip()
            tokens = res_data.get("usage", {}).get("completion_tokens", 0)
            tps = round(tokens / elapsed, 1) if elapsed > 0 and tokens else "?"
            return {"ok": True, "reply": reply, "elapsed_s": elapsed, "tps": tps}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------
# Model & Binary Discovery
# --------------------------------------------------------------------------

def find_binary(candidate_list, fallback_keyword="server"):
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
    """
    Recursively scans for GGUF weights, intelligently separating
    text language models from multimodal vision projectors (mmproj).
    """
    models = []
    projectors = []
    for path in BASE_DIR.rglob("*.gguf"):
        try:
            size_mb = path.stat().st_size / (1024 * 1024)
        except OSError:
            size_mb = 0
        
        info = {
            "path": path,
            "name": path.name,
            "rel": str(path.relative_to(BASE_DIR)),
            "size_mb": round(size_mb, 1),
            "is_mmproj": "mmproj" in path.name.lower(),
        }
        if info["is_mmproj"]:
            projectors.append(info)
        else:
            models.append(info)

    models.sort(key=lambda m: m["rel"].lower())
    projectors.sort(key=lambda p: p["rel"].lower())
    return {"models": models, "projectors": projectors}


# --------------------------------------------------------------------------
# Dependency & Terminal QR Code
# --------------------------------------------------------------------------

def ensure_dependencies():
    global DEPS_PERMANENTLY_SKIPPED
    try:
        import qrcode
    except ImportError:
        if DEPS_PERMANENTLY_SKIPPED:
            return
        draw_box("Optional Enhancements", "Zero-Dependency Mobile QR Setup")
        explain("Install optional 'qrcode' to render scan-to-connect ASCII QR codes for mobile devices.")
        print(f"    {UI.CYAN}[y]{UI.RESET}{UI.BASE} Install qrcode ({PIP_TIMEOUT_SECONDS}s timeout)")
        print(f"    {UI.CYAN}[n]{UI.RESET}{UI.BASE} Skip for now")
        print(f"    {UI.CYAN}[s]{UI.RESET}{UI.BASE} Skip permanently for this session")
        k = read_single_key("Choice: ", ["y", "n", "s"])
        if k == "s":
            DEPS_PERMANENTLY_SKIPPED = True
            return
        if k == "y":
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "--quiet", "qrcode"],
                    timeout=PIP_TIMEOUT_SECONDS
                )
                print(f"    {UI.TAG_OK} Successfully installed qrcode.")
            except Exception:
                print(f"    {UI.TAG_WARN} Offline or install timed out. Continuing seamlessly.")
            time.sleep(1)


def print_qr(url):
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        print()
        qr.print_ascii(invert=True)
        print()
    except ImportError:
        print(f"\n    {UI.MUTED}(Point your mobile browser to: {UI.CYAN}{url}{UI.MUTED}){UI.RESET}{UI.BASE}\n")


# --------------------------------------------------------------------------
# Model Selection Card
# --------------------------------------------------------------------------

def choose_model(default_path=None):
    scan = scan_models()
    models = scan["models"]
    if not models:
        print(f"\n    {UI.TAG_FAIL} No language model .gguf files discovered in {BASE_DIR}.")
        print(f"    {UI.MUTED}Drop any .gguf file into this folder and reload.{UI.RESET}{UI.BASE}")
        return None

    explain("Select a model. Smaller parameter models (1B-3B) run fast on CPU; 7B+ offer deeper logic.")
    print(f"    {UI.BOLD}Discovered LLM Weights:{UI.RESET}{UI.BASE}\n")
    default_idx = 1
    for i, m in enumerate(models, 1):
        is_def = default_path and str(m["path"]) == str(default_path)
        marker = f" {UI.EMERALD}◀ last used{UI.RESET}{UI.BASE}" if is_def else ""
        if is_def:
            default_idx = i
        print(f"     {UI.CYAN}{i:2d}.{UI.RESET} {UI.WHITE}{m['rel']:<42}{UI.RESET} {UI.MUTED}({m['size_mb']} MB){UI.RESET}{marker}")
    print()

    res = prompt("Pick model number", default=str(default_idx))
    if res in ("__back__", "__quit__"):
        return res
    if res == "__skip__":
        return models[default_idx - 1]["path"]
    try:
        val = int(res)
        if 1 <= val <= len(models):
            return models[val - 1]["path"]
    except ValueError:
        pass
    return models[default_idx - 1]["path"]


# --------------------------------------------------------------------------
# Config & Profile Store
# --------------------------------------------------------------------------

def load_json(path, fallback):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return fallback
    return fallback


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_last_config():
    return load_json(CONFIG_FILE, {
        "model": None,
        "port": None,
        "host": DEFAULT_HOST,
        "ctx": DEFAULT_CTX,
        "threads": DEFAULT_THREADS,
        "parallel": DEFAULT_PARALLEL,
        "ngl": 0,
        "fa": False,
        "embedding": False,
    })


def save_last_config(cfg):
    save_json(CONFIG_FILE, cfg)


def profiles_menu():
    while True:
        draw_box("Server Profiles", "Saved Launch Configurations")
        breadcrumb("Home", "Server Profiles")
        profiles = load_json(PROFILES_FILE, {})

        if not profiles:
            print(f"    {UI.MUTED}No saved profiles found. Save current config to create one.{UI.RESET}{UI.BASE}\n")
        else:
            for i, (name, cfg) in enumerate(profiles.items(), 1):
                m_name = Path(cfg.get('model', '?')).name
                print(f"     {UI.CYAN}{i:2d}.{UI.RESET} {UI.WHITE}{name:<16}{UI.RESET} {UI.PURPLE}➔{UI.RESET} {UI.AQUA}{m_name:<28}{UI.RESET} {UI.MUTED}[port={cfg.get('port')}, ctx={cfg.get('ctx')}, t={cfg.get('threads')}]{UI.RESET}")
            print()

        print(f"    {UI.CYAN}[n]{UI.RESET} Save active settings as new profile")
        print(f"    {UI.CYAN}[l]{UI.RESET} Load profile and launch server")
        print(f"    {UI.CYAN}[d]{UI.RESET} Delete profile")
        print(f"    {UI.CYAN}[q]{UI.RESET} Back to main menu\n")

        k = read_single_key("Select action: ", ["n", "l", "d", "q"])
        if k in ("q", "__back__", ""):
            return
        elif k == "n":
            cfg = load_last_config()
            if not cfg.get("model"):
                print(f"\n    {UI.TAG_WARN} Run Quick Start first to establish a configuration.")
                time.sleep(1.5)
                continue
            name = prompt("Profile name (e.g. 'quick-chat', 'deep-reason')")
            if name and name not in ("__back__", "__quit__"):
                profiles[name] = cfg
                save_json(PROFILES_FILE, profiles)
                print(f"    {UI.TAG_OK} Profile saved.")
                time.sleep(1)
        elif k == "l":
            if not profiles:
                continue
            name = prompt("Profile name to load")
            if name in profiles:
                p = profiles[name]
                start_server(
                    p["model"], p.get("port"), p.get("host", DEFAULT_HOST),
                    ctx=p.get("ctx", DEFAULT_CTX),
                    threads=p.get("threads", DEFAULT_THREADS),
                    parallel=p.get("parallel", DEFAULT_PARALLEL),
                    ngl=p.get("ngl", 0),
                    fa=p.get("fa", False),
                    embedding=p.get("embedding", False)
                )
                input(f"\n    {UI.MUTED}Press Enter to continue...{UI.RESET}")
        elif k == "d":
            if not profiles:
                continue
            name = prompt("Profile name to delete")
            if name in profiles:
                del profiles[name]
                save_json(PROFILES_FILE, profiles)
                print(f"    {UI.TAG_OK} Deleted.")
                time.sleep(1)


# --------------------------------------------------------------------------
# Multi-Tool Wrappers: llama-cli & llama-bench
# --------------------------------------------------------------------------

def run_console_chat():
    draw_box("Direct Terminal Chat", "Powered by llama-cli.exe")
    breadcrumb("Home", "Direct Terminal Chat")
    explain("Conversational inference directly in this terminal session without starting a background server.")

    cli_exe = find_cli_exe()
    if not cli_exe:
        print(f"    {UI.TAG_FAIL} llama-cli.exe not found in {BASE_DIR}.")
        input(f"\n    {UI.MUTED}Press Enter to return...{UI.RESET}")
        return

    last_cfg = load_last_config()
    model_path = choose_model(default_path=last_cfg.get("model"))
    if not model_path or model_path in ("__back__", "__quit__"):
        return

    t_val = prompt("CPU Threads", default=str(DEFAULT_THREADS))
    if t_val in ("__back__", "__quit__"):
        return

    cmd = [
        str(cli_exe),
        "-m", str(model_path),
        "-cnv",
        "--threads", str(t_val),
        "--simple-io",
    ]

    print(f"\n    {UI.TAG_INFO} Launching conversation. Press Ctrl+C or type /exit to end.\n")
    try:
        subprocess.run(cmd, cwd=str(BASE_DIR))
    except KeyboardInterrupt:
        print(f"\n    {UI.TAG_INFO} Interactive chat session ended.")
    except Exception as e:
        print(f"\n    {UI.TAG_FAIL} Error running llama-cli: {e}")

    input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}")


def run_hardware_benchmark():
    draw_box("Hardware Inference Benchmark", "Powered by llama-bench.exe")
    breadcrumb("Home", "Hardware Benchmark")
    explain("Calculates prompt processing (PP) and text generation (TG) throughput in tokens/second.")

    bench_exe = find_bench_exe()
    if not bench_exe:
        print(f"    {UI.TAG_FAIL} llama-bench.exe not found in {BASE_DIR}.")
        input(f"\n    {UI.MUTED}Press Enter to return...{UI.RESET}")
        return

    last_cfg = load_last_config()
    model_path = choose_model(default_path=last_cfg.get("model"))
    if not model_path or model_path in ("__back__", "__quit__"):
        return

    reps = prompt("Repetitions per test", default="2")
    if reps in ("__back__", "__quit__"):
        return

    cmd = [
        str(bench_exe),
        "-m", str(model_path),
        "-p", "512",
        "-n", "128",
        "-r", str(reps),
        "-t", str(DEFAULT_THREADS),
        "-o", "md",
    ]

    print(f"\n    {UI.TAG_INFO} Executing hardware benchmark harness...\n")
    try:
        res = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True)
        if res.returncode == 0:
            print(res.stdout)
            print(f"    {UI.TAG_OK} Benchmark run completed successfully.")
        else:
            if res.stdout:
                print(res.stdout)
            if res.stderr:
                print(res.stderr)
    except KeyboardInterrupt:
        print(f"\n    {UI.TAG_WARN} Benchmark aborted.")
    except Exception as e:
        print(f"\n    {UI.TAG_FAIL} Execution failed: {e}")

    input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}")


# --------------------------------------------------------------------------
# Server Supervisor (Daemon Management)
# --------------------------------------------------------------------------

def start_server(model_path, port, host, ctx=DEFAULT_CTX, threads=DEFAULT_THREADS, parallel=DEFAULT_PARALLEL, ngl=0, fa=False, embedding=False, mmproj_path=None):
    global SERVER_PROC, SERVER_LOG_PATH, SERVER_INFO

    if SERVER_PROC and SERVER_PROC.poll() is None:
        print(f"\n    {UI.TAG_WARN} Active server is already running. Stop it before starting another.")
        return False

    exe = find_exe()
    if not exe:
        print(f"\n    {UI.TAG_FAIL} ERROR: llama-server executable not found.")
        return False

    model_path = Path(model_path)
    if not model_path.exists():
        print(f"\n    {UI.TAG_FAIL} ERROR: Model file not found: {model_path}")
        return False

    if not port or not is_port_free(port):
        new_port = get_random_free_port()
        if port:
            print(f"    {UI.TAG_WARN} Port {port} occupied. Reassigned to free port {new_port}.")
        port = new_port

    prune_logs(MAX_LOG_RETENTION)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"server-{timestamp}.log"

    cmd = [
        str(exe),
        "-m", str(model_path),
        "--host", host,
        "--port", str(port),
        "-c", str(ctx),
        "-t", str(threads),
        "-np", str(parallel),
    ]

    if ngl and int(ngl) > 0:
        cmd.extend(["-ngl", str(ngl)])
    if fa:
        cmd.extend(["-fa", "on"])
    if embedding:
        cmd.append("--embedding")
    if mmproj_path and Path(mmproj_path).exists():
        cmd.extend(["--mmproj", str(mmproj_path)])

    print(f"\n    {UI.TAG_INFO} Launching llama-server daemon...")
    print(f"      Model      : {UI.WHITE}{model_path.name}{UI.RESET}")
    print(f"      Address    : {UI.CYAN}{host}:{port}{UI.RESET}")
    print(f"      Context    : {UI.AQUA}{ctx} tokens{UI.RESET} | Threads: {UI.AQUA}{threads}{UI.RESET} | Slots: {UI.AQUA}{parallel}{UI.RESET}")
    if ngl:
        print(f"      GPU Layers : {UI.AMBER}{ngl}{UI.RESET}")

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
        # Immediately close handle in parent; child inherited the OS file descriptor
        log_handle.close()
    except Exception as e:
        print(f"\n    {UI.TAG_FAIL} Daemon spawn error: {e}")
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
        "parallel": parallel,
        "ngl": ngl,
        "fa": fa,
        "embedding": embedding,
        "started_at": timestamp,
    }

    # Closed-Loop Readiness Check against /health
    ready, err = wait_for_server_ready(proc, port, timeout_s=25)
    if not ready:
        print(f"\n    {UI.TAG_FAIL} Server initialization failed: {err}")
        print(f"    {UI.MUTED}Inspect output log: {log_path}{UI.RESET}")
        return False

    save_last_config(SERVER_INFO)

    ip = get_lan_ip()
    url = f"http://{ip}:{port}"
    local_url = f"http://localhost:{port}"

    print(f"\n    {UI.TAG_OK} {UI.EMERALD}{UI.BOLD}LOCAL AI NODE IS CONFIRMED READY.{UI.RESET}")
    print(f"      Workstation Browser : {UI.CYAN}{local_url}{UI.RESET}")
    print(f"      LAN Access Address  : {UI.EMERALD}{url}{UI.RESET}")
    print(f"      OpenAI API Base     : {UI.WHITE}http://{ip}:{port}/v1{UI.RESET}\n")
    print_qr(url)

    # Offer immediate 1-click test prompt
    print(f"    {UI.BOLD}Verification Check:{UI.RESET}")
    print(f"     {UI.CYAN}[y]{UI.RESET} Send quick test prompt directly to verify inference")
    print(f"     {UI.CYAN}[Enter / n]{UI.RESET} Continue to main menu")
    test_k = read_single_key("Action: ", ["y", "n", ""])
    if test_k == "y":
        print(f"\n    {UI.TAG_INFO} Transmitting test prompt to /v1/chat/completions...")
        res = query_chat_test(port)
        if res["ok"]:
            print(f"    {UI.TAG_OK} {UI.BOLD}Model Response ({res['elapsed_s']}s, {res['tps']} t/s):{UI.RESET}")
            print(f"    {UI.WHITE}\"{res['reply']}\"{UI.RESET}\n")
        else:
            print(f"    {UI.TAG_FAIL} Test query failed: {res['error']}\n")

    return True


def stop_server():
    global SERVER_PROC, SERVER_INFO
    if not SERVER_PROC or SERVER_PROC.poll() is not None:
        print(f"\n    {UI.TAG_INFO} No active server daemon running in this session.")
        SERVER_PROC = None
        return
    print(f"\n    {UI.TAG_INFO} Halting llama-server daemon...")
    SERVER_PROC.terminate()
    try:
        SERVER_PROC.wait(timeout=5)
    except subprocess.TimeoutExpired:
        SERVER_PROC.kill()
    print(f"    {UI.TAG_OK} Server halted.")
    SERVER_PROC = None
    SERVER_INFO = {}


def kill_zombie_servers():
    draw_box("Zombie Process Cleaner", "Defensive Port & Memory Reclamation")
    breadcrumb("Home", "Kill Lingering Nodes")
    explain("Terminates any abandoned background llama-server processes locking ports or memory.")

    print(f"    {UI.CYAN}[y]{UI.RESET} Force-kill all running llama-server.exe instances")
    print(f"    {UI.CYAN}[n]{UI.RESET} Cancel and return to menu\n")
    k = read_single_key("Action: ", ["y", "n"])
    if k != "y":
        return

    if IS_WINDOWS:
        try:
            res = subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True, text=True)
            if "SUCCESS" in res.stdout or "PID" in res.stdout:
                print(f"    {UI.TAG_OK} Terminated lingering server instances.")
            else:
                print(f"    {UI.TAG_INFO} No lingering llama-server instances found.")
        except Exception as e:
            print(f"    {UI.TAG_FAIL} Taskkill error: {e}")
    else:
        try:
            subprocess.run(["pkill", "-9", "-f", "llama-server"], check=False)
            print(f"    {UI.TAG_OK} Sent SIGKILL signals.")
        except Exception as e:
            print(f"    {UI.TAG_FAIL} Error: {e}")

    global SERVER_PROC, SERVER_INFO
    SERVER_PROC = None
    SERVER_INFO = {}
    time.sleep(1.2)


# --------------------------------------------------------------------------
# Node Telemetry & Real-Time Telemetry Card
# --------------------------------------------------------------------------

def server_status_menu():
    while True:
        draw_box("Node Telemetry & Control", "Live Server Monitor")
        breadcrumb("Home", "Node Telemetry")

        if SERVER_PROC and SERVER_PROC.poll() is None:
            port = SERVER_INFO.get("port")
            ip = get_lan_ip()

            # Execute fast non-blocking live /health audit (0.4s timeout)
            h = probe_server_health(port, timeout_s=0.4)
            if h.get("status") == "ok":
                health_badge = f"{UI.EMERALD}● ONLINE ({h['latency_ms']}ms){UI.RESET}"
                slots_info = f"Slots: {h['body'].get('slots_idle', '?')}/{h['body'].get('slots_processing', '?') + h['body'].get('slots_idle', 1)} idle"
            else:
                health_badge = f"{UI.AMBER}▲ STARTING / BUSY{UI.RESET}"
                slots_info = "Awaiting first response"

            print(f"    Daemon State  : {health_badge} [{slots_info}]")
            print(f"    Active Model  : {UI.WHITE}{Path(SERVER_INFO.get('model', '?')).name}{UI.RESET}")
            print(f"    Binding       : {UI.CYAN}{SERVER_INFO.get('host')}:{port}{UI.RESET}")
            print(f"    Local Web UI  : {UI.AQUA}http://localhost:{port}{UI.RESET}")
            print(f"    LAN Access URL: {UI.EMERALD}http://{ip}:{port}{UI.RESET}")
            print(f"    Config Preset : {UI.MUTED}ctx={SERVER_INFO.get('ctx')}, threads={SERVER_INFO.get('threads')}, slots={SERVER_INFO.get('parallel')}{UI.RESET}\n")

            print(f"    {UI.CYAN}[t]{UI.RESET} Test API Connection (In-Terminal Chat Ping)")
            print(f"    {UI.CYAN}[q]{UI.RESET} Show Terminal QR Code again")
            print(f"    {UI.CYAN}[l]{UI.RESET} Launch Live Log Stream in new window")
            print(f"    {UI.ROSE}[x]{UI.RESET} Stop Server Immediately")
            print(f"    {UI.MUTED}[Esc / Enter] Back to Main Menu{UI.RESET}\n")

            k = read_single_key("Action: ", ["t", "q", "l", "x", ""])
            if k in ("", "__back__"):
                return
            elif k == "t":
                print(f"\n    {UI.TAG_INFO} Transmitting test payload to /v1/chat/completions...")
                res = query_chat_test(port)
                if res["ok"]:
                    print(f"    {UI.TAG_OK} {UI.BOLD}Model Response ({res['elapsed_s']}s, {res['tps']} t/s):{UI.RESET}")
                    print(f"    {UI.WHITE}\"{res['reply']}\"{UI.RESET}")
                else:
                    print(f"    {UI.TAG_FAIL} Request failed: {res['error']}")
                input(f"\n    {UI.MUTED}Press Enter to continue...{UI.RESET}")
            elif k == "q":
                print_qr(f"http://{ip}:{port}")
                input(f"    {UI.MUTED}Press Enter to continue...{UI.RESET}")
            elif k == "l":
                if IS_WINDOWS:
                    cmd_str = f'start powershell -NoExit -Command "Get-Content -Path \'{SERVER_LOG_PATH}\' -Wait -Tail 25"'
                    os.system(cmd_str)
                    print(f"\n    {UI.TAG_OK} Spawned live log monitoring window.")
                else:
                    print(f"\n    Run: tail -f \"{SERVER_LOG_PATH}\"")
                time.sleep(1.5)
            elif k == "x":
                stop_server()
                time.sleep(1)
        else:
            print(f"    Server Status: {UI.DARK}○ OFFLINE (Daemon Not Running){UI.RESET}\n")
            input(f"    {UI.MUTED}Press Enter to return to menu...{UI.RESET}")
            return


# --------------------------------------------------------------------------
# Quick Start Wizard
# --------------------------------------------------------------------------

def quick_start():
    draw_box("Rapid Deployment Wizard", "4-Step Air-Gapped Setup")
    breadcrumb("Home", "Quick Start")
    explain("Guided setup to launch a background LAN server. Select model, network port, and optional tuning.")

    last_cfg = load_last_config()
    scan = scan_models()
    model_path = choose_model(default_path=last_cfg.get("model"))
    if not model_path or model_path in ("__back__", "__quit__"):
        return

    # Check for vision multimodal projector companion
    mmproj_path = None
    if scan["projectors"]:
        explain("Multimodal vision projector detected. Attach vision capability?")
        print(f"    {UI.CYAN}[y]{UI.RESET} Attach {scan['projectors'][0]['name']}")
        print(f"    {UI.CYAN}[n]{UI.RESET} Skip vision projector (Standard Text Only)")
        vk = read_single_key("Choice: ", ["y", "n"])
        if vk == "y":
            mmproj_path = scan["projectors"][0]["path"]

    safe_port = get_random_free_port()
    port_input = prompt("TCP Port", default=str(safe_port))
    if port_input in ("__back__", "__quit__"):
        return
    try:
        port = int(port_input)
    except ValueError:
        port = safe_port

    print(f"\n    {UI.BOLD}Network Exposure:{UI.RESET}")
    print(f"     {UI.CYAN}1.{UI.RESET} Entire Local Network (0.0.0.0) [Phones, Tablets on Wi-Fi/Hotspot]")
    print(f"     {UI.CYAN}2.{UI.RESET} Workstation Only (127.0.0.1)")
    host_choice = read_single_key("Select [1]: ", ["1", "2", ""])
    host = "127.0.0.1" if host_choice == "2" else "0.0.0.0"

    # Hardware Tuning Stage
    ctx = last_cfg.get("ctx", DEFAULT_CTX)
    threads = last_cfg.get("threads", DEFAULT_THREADS)
    ngl = last_cfg.get("ngl", 0)
    fa = last_cfg.get("fa", False)

    print(f"\n    {UI.BOLD}Hardware Tuning:{UI.RESET}")
    print(f"     {UI.CYAN}[y]{UI.RESET} Tune Context, CPU Cores, and GPU Offload")
    print(f"     {UI.CYAN}[Enter / n]{UI.RESET} Use Recommended Defaults (ctx=2048, threads={DEFAULT_THREADS})")
    tune_k = read_single_key("Action: ", ["y", "n", ""])
    if tune_k == "y":
        c_val = prompt("Context size (tokens)", default=str(ctx))
        t_val = prompt("CPU Threads", default=str(threads))
        ngl_val = prompt("GPU Layers to offload (-ngl)", default=str(ngl))
        fa_k = read_single_key("Enable Flash Attention? [y/N]: ", ["y", "n", ""])
        try:
            ctx = int(c_val)
            threads = int(t_val)
            ngl = int(ngl_val)
            fa = fa_k == "y"
        except ValueError:
            pass

    start_server(model_path, port, host, ctx=ctx, threads=threads, ngl=ngl, fa=fa, mmproj_path=mmproj_path)
    input(f"\n    {UI.MUTED}Press Enter to return to menu...{UI.RESET}")


# --------------------------------------------------------------------------
# Main Menu Matrix
# --------------------------------------------------------------------------

def main_menu():
    while True:
        clear_screen()
        sys.stdout.write(UI.BASE)

        running = SERVER_PROC is not None and SERVER_PROC.poll() is None
        if running:
            h = probe_server_health(SERVER_INFO.get("port"), timeout_s=0.3)
            latency_str = f" | {h['latency_ms']}ms" if h.get("status") == "ok" else ""
            status_badge = f"{UI.EMERALD}● ONLINE{latency_str}{UI.RESET}{UI.BASE}"
        else:
            status_badge = f"{UI.DARK}○ OFFLINE{UI.RESET}{UI.BASE}"

        print(f"  {UI.CYAN}╭────────────────────────────────────────────────────────────────────╮{UI.RESET}{UI.BASE}")
        print(f"  {UI.CYAN}│{UI.RESET}{UI.BASE}   {UI.WHITE}{UI.BOLD}⬡ LOCAL AI NODE ARCHITECTURE{UI.RESET}{UI.BASE}   [{status_badge}]                 {UI.CYAN}│{UI.RESET}{UI.BASE}")
        print(f"  {UI.CYAN}│{UI.RESET}{UI.BASE}   {UI.MUTED}Directory: {str(BASE_DIR)[-48:]:<48}{UI.RESET}{UI.BASE} {UI.CYAN}│{UI.RESET}{UI.BASE}")
        print(f"  {UI.CYAN}╰────────────────────────────────────────────────────────────────────╯{UI.RESET}{UI.BASE}\n")

        print(f"    {UI.EMERALD}EXECUTION ENGINES:{UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}1.{UI.RESET}{UI.BASE} {UI.WHITE}Quick Start Server{UI.RESET}     {UI.DARK}(launch background LAN server with QR code){UI.RESET}")
        print(f"     {UI.CYAN}2.{UI.RESET}{UI.BASE} {UI.WHITE}Direct Terminal Chat{UI.RESET}   {UI.DARK}(chat with model in terminal via llama-cli){UI.RESET}")
        print(f"     {UI.CYAN}3.{UI.RESET}{UI.BASE} {UI.WHITE}Hardware Benchmark{UI.RESET}     {UI.DARK}(evaluate prompt & eval speed via llama-bench){UI.RESET}\n")

        print(f"    {UI.EMERALD}NODE TELEMETRY & MANAGEMENT:{UI.RESET}{UI.BASE}")
        print(f"     {UI.CYAN}4.{UI.RESET}{UI.BASE} {UI.WHITE}Node Status / Monitor{UI.RESET}  {UI.DARK}(health telemetry, test ping, live stream){UI.RESET}")
        print(f"     {UI.CYAN}5.{UI.RESET}{UI.BASE} {UI.WHITE}Manage Models{UI.RESET}          {UI.DARK}(inspect GGUF models & vision projectors){UI.RESET}")
        print(f"     {UI.CYAN}6.{UI.RESET}{UI.BASE} {UI.WHITE}Server Profiles{UI.RESET}        {UI.DARK}(save/load named configs and hardware presets){UI.RESET}")
        print(f"     {UI.CYAN}7.{UI.RESET}{UI.BASE} {UI.WHITE}Kill Lingering Nodes{UI.RESET}   {UI.DARK}(clean up orphaned background llama-server processes){UI.RESET}")
        print(f"     {UI.ROSE}0.{UI.RESET}{UI.BASE} {UI.WHITE}Exit Session{UI.RESET}           {UI.DARK}(compulsory graceful halt){UI.RESET}\n")

        k = read_single_key("Command › ", ["1", "2", "3", "4", "5", "6", "7", "0"])

        if k == "1":
            quick_start()
        elif k == "2":
            run_console_chat()
        elif k == "3":
            run_hardware_benchmark()
        elif k == "4":
            server_status_menu()
        elif k == "5":
            draw_box("Model Matrix", "Discovered GGUF Weights")
            scan = scan_models()
            models = scan["models"]
            projectors = scan["projectors"]
            print(f"    {UI.TAG_OK} Discovered {len(models)} Language Model(s) and {len(projectors)} Vision Projector(s):\n")
            for i, m in enumerate(models, 1):
                print(f"     {UI.CYAN}{i:2d}.{UI.RESET} {UI.WHITE}{m['rel']:<45}{UI.RESET} {UI.MUTED}({m['size_mb']} MB){UI.RESET}")
            if projectors:
                print(f"\n    {UI.AMBER}Vision Projectors (--mmproj):{UI.RESET}")
                for p in projectors:
                    print(f"     {UI.PURPLE}◈{UI.RESET} {UI.WHITE}{p['rel']:<45}{UI.RESET} {UI.MUTED}({p['size_mb']} MB){UI.RESET}")
            input(f"\n    {UI.MUTED}Press Enter to return...{UI.RESET}")
        elif k == "6":
            profiles_menu()
        elif k == "7":
            kill_zombie_servers()
        elif k == "0":
            if SERVER_PROC and SERVER_PROC.poll() is None:
                print(f"\n    {UI.TAG_WARN} Terminating server daemon...")
                stop_server()
            print(f"\n    {UI.CYAN}Session terminated cleanly.{UI.RESET}\n")
            break


# --------------------------------------------------------------------------
# Entry Point
# --------------------------------------------------------------------------

def main():
    sys.stdout.write(UI.BASE)
    ensure_dependencies()
    try:
        main_menu()
    except KeyboardInterrupt:
        print(f"\n\n    {UI.TAG_WARN} Session interrupted.")
        cleanup_on_exit()
        print(f"{UI.RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()