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
