# Technical Documentation: Local AI Node Architecture

## System Overview
Local AI Node is an offline-first orchestration controller and terminal management hub for hosting large language models over local area networks (LAN). It acts as a resilient supervisor process for the upstream `llama-server` runtime from the `llama.cpp` project.

## Core Architectural Pillars

### 1. Zero-Connectivity / Air-Gapped Operation
- Built on Python standard library modules (`socket`, `subprocess`, `ctypes`, `pathlib`, `shutil`, `json`).
- All network operations are strictly local. LAN IP resolution utilizes non-routable UDP socket queries (`socket.connect(('8.8.8.8', 80))`) without sending network packets, which functions seamlessly without WAN or internet access.
- Optional dependencies (`qrcode`, `psutil`) degrade gracefully if missing; dependency installers enforce tight timeouts (8 seconds) to prevent hangs when offline.

### 2. Network & Process Lifecycle
- **Dynamic Port Selection**: Mitigates socket collision by scanning randomly within ephemeral port ranges (10000-60000) and verifying binding availability (`connect_ex`).
- **Daemon Supervision**: Spawns `llama-server.exe` under Windows `CREATE_NO_WINDOW` flags, piping combined standard streams into timestamped log files located under `logs/`.
- **Compulsory Teardown**: Implements structured termination hooks (`SIGINT` and exit traps) to guarantee child processes are halted upon console exit.

### 3. Client Onboarding
- **Terminal QR Code Handshake**: Renders an in-console ASCII QR code containing the full LAN URL (`http://<LAN_IP>:<PORT>`), enabling camera-to-browser connection for mobile phones and tablets connected to the local Wi-Fi or ad-hoc mobile hotspot.
- **OpenAI-Compatible REST Interface**: Inherits full `/v1/chat/completions`, `/v1/models`, and `/v1/embeddings` API support exposed by the underlying `llama-server` instance.

### 4. System Diagnostics Engine
Non-blocking local audit verifying:
- Presence of valid inference binaries (`llama-server.exe`, `server.exe`).
- Recursive discovery of valid `.gguf` quantized model files.
- Port listening state.
- Windows Advanced Firewall inbound rule posture (`netsh advfirewall`).
- Disk headroom for memory-mapped file paging.
- RAM availability via system performance monitors.

### 5. Multi-Tool Engine Integration & Performance Tuning (Setup-Engine)
- **Unified Command Center**: Replaced legacy standalone script with `Setup-Engine.py`, exposing cohesive interactive wrappers around primary `llama.cpp` toolchains.
- **Direct Terminal Chat Integration (`llama-cli`)**: Spawns interactive conversation sessions directly in the active shell without launching the HTTP server daemon or web browser. Uses `--conversation` (`-cnv`) and `--simple-io` for terminal compatibility.
- **Hardware Performance Profiling (`llama-bench`)**: Wraps the compiled benchmarking tool to evaluate prompt throughput (`PP` tokens/sec) and generation speed (`TG` tokens/sec) across local CPU/GPU hardware configurations.
- **Parametric Inference Tuning**: Permits runtime configuration of context window size (`-c`), CPU core allocation (`-t`), GPU layer offloading (`-ngl`), and Flash Attention (`-fa`) during server deployment and within persistent profile presets (`server_profiles.json`).
- **Defensive Process Hygiene**: Integrates Python `atexit` registration and interactive zombie process termination (`taskkill` / `pkill`) to prevent port locking or resource exhaustion from orphaned server daemons.

### 6. Interactive CLI UI Architecture & Native Endpoints Integration
- **Single-Stroke Event Loop (`msvcrt.getch()`)**: Eliminated "type-and-enter" friction on Windows terminals via non-blocking single-character captures, permitting instant numeric and hotkey selection while degrading safely to standard line input in piped/non-TTY environments.
- **Card Framing & Spatial Layout**: Transitioned to Unicode rounded box framing (`╭─╮`, `│`, `╰─╯`) and breadcrumb hierarchy (`Home › Submenu`) for reduced cognitive load.
- **Live HTTP Health & Telemetry Probing (`/health`)**: Actively polls the local `llama-server` HTTP daemon with microsecond timers to report true roundtrip latency in milliseconds and real-time slot processing state.
- **In-Terminal REST API Completion Client**: Houses a native test harness sending standard JSON payloads to `/v1/chat/completions` via Python standard library `urllib.request`. Displays response text, duration, and generated tokens per second (`t/s`) without launching a browser.
- **Multimodal Projector Recognition**: Segregates standard LLM weights from vision projection matrices (`mmproj*.gguf`), enabling selective `--mmproj` attachment for multimodal models.

### 7. Production Hardening: Closed-Loop Readiness & Handle Lifecycle
- **Closed-Loop Readiness Probe (`wait_for_server_ready`)**: Replaced the static, arbitrary 2-second sleep with an active polling loop against `/health` featuring an animated console spinner. Guarantees that the QR code and operational URLs are rendered strictly after tensor weights are loaded and the HTTP socket returns 200 OK.
- **Parent File Descriptor Reclamation**: Explicitly invokes `log_handle.close()` immediately following `subprocess.Popen()`. Prevents descriptor locking on Windows while allowing the child server process to retain exclusive writing capability via its inherited handle.
- **Automatic Log File Rotation (`prune_logs`)**: Automatically bounds file count under `logs/` to the 10 most recent session files, eliminating disk entropy and unconstrained log accumulation.
- **Win32 Console Control Hook (`SetConsoleCtrlHandler`)**: Registers an unmanaged Windows console event handler via `ctypes` targeting `CTRL_CLOSE_EVENT` and shutdown signals, ensuring child `llama-server.exe` processes are halted even if the host terminal window is clicked closed via the OS window frame `[X]` button.
- **Immediate Post-Boot Verification Prompt**: Prompts the operator with an optional single-stroke prompt test (`[y]`) immediately upon confirmation of socket binding, enabling instant inference validation before external clients connect.
