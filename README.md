# Local AI Node: Offline llama.cpp LAN Hub

[![Inference Engine](https://img.shields.io/badge/Inference_Engine-llama.cpp-0055ff.svg)](https://github.com/ggml-org/llama.cpp)
[![Format](https://img.shields.io/badge/Model_Format-GGUF-brightgreen.svg)](https://github.com/ggml-org/ggml)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#prerequisites)

> **Attribution**: Powered by [llama.cpp](https://github.com/ggml-org/llama.cpp) created by Georgi Gerganov and the GGML team. This project provides a terminal control center and network manager to host and access local LLMs across your local area network without internet dependencies.

---

## What is Local AI Node?

Local AI Node turns any laptop or desktop into an offline private AI server. It wraps `llama.cpp`'s HTTP server in an interactive terminal dashboard, scans your drives for `.gguf` models, handles dynamic port assignment to avoid conflicts, and displays a terminal ASCII QR code so phones and tablets on your Wi-Fi or mobile hotspot can connect instantly.

Built specifically for **ultra-remote, air-gapped, and zero-connectivity environments** where cloud AI is unavailable or prohibited.

---

## Architecture Flow

```text
 +-------------------------------------------------------------+
 |                   Local AI Node Controller                  |
 |                   (Setup-Engine.py - TUI)                   |
 +------------------------------+------------------------------+
                                |
        +-----------------------+-----------------------+
        |                                               |
        v                                               v
 +----------------------------+           +----------------------------+
 |     System Diagnostics     |           |     Multi-Tool Supervisor  |
 | - Binary discovery         |           | - llama-server daemon      |
 | - Port availability        |           | - llama-cli chat session   |
 | - RAM & Disk headroom      |           | - llama-bench profiler     |
 | - Windows Firewall audit   |           | - Rotated logs (logs/)     |
 +----------------------------+           +--------------+-------------+
                                                         |
                                                         v
                                          +----------------------------+
                                          |      llama-server.exe      |
                                          |   (GGML / GGUF CPU Core)   |
                                          +--------------+-------------+
                                                         |
                         +-------------------------------+-------------------------------+
                         |                                                               |
                         v                                                               v
          +-----------------------------+                                 +-----------------------------+
          |      Local Workstation      |                                 |       LAN Mobile Device     |
          |    http://localhost:<PORT>  |                                 |    http://<LAN_IP>:<PORT>   |
          |    (Browser / Desktop App)  |                                 |    (Camera QR Scan / Wi-Fi) |
          +-----------------------------+                                 +-----------------------------+
```

---

## Interactive Quick Start

### 1. Requirements

- **Python 3.8+** (Standard installation &mdash; no mandatory external packages)
- **llama-server binary**: Download from [llama.cpp Releases](https://github.com/ggml-org/llama.cpp/releases)
- **GGUF Model**: Download any `.gguf` file (e.g. Llama 3.2 1B, Qwen 2.5 1.5B)

### 2. Launch the Control Center

Place `Setup-Engine.py` in the same directory as your `llama-server.exe`:

```bash
python Setup-Engine.py
```

### 3. Choose from the Core Engine Menu

1. **Quick Start Server**:
   - Pick a model (auto-detected recursively).
   - Confirm collision-free port.
   - Choose network binding (`0.0.0.0` for LAN / mobile devices, or `127.0.0.1` for local only).
   - Optional hardware tuning (context size `-c`, CPU threads `-t`, GPU layers `-ngl`, Flash Attention `-fa`).
   - Connect on desktop (`http://localhost:<port>`) or mobile (scan terminal QR code).
2. **Direct Terminal Chat (`llama-cli`)**:
   - Converse with any downloaded `.gguf` model right in your console without running a server daemon or web browser.
3. **Hardware Benchmark (`llama-bench`)**:
   - Instant token speed profiling for prompt processing (`PP`) and text generation (`TG`).
4. **Kill Lingering Nodes**:
   - Clean up orphaned background `llama-server.exe` processes holding ports or memory.

---

## Directory Setup

A standard folder arrangement:

```text
├── Setup-Engine.py              # Local AI Node interactive control center
├── requirements.txt             # Optional enhancement packages
├── LICENSE                      # Project license and upstream attribution
├── README.md                    # Documentation
├── documentation.md             # Technical architecture and developer reference
├── llama-server.exe             # Upstream llama.cpp server binary
├── llama-cli.exe                # Upstream direct CLI inference tool
├── llama-bench.exe              # Upstream hardware benchmark tool
├── ggml-*.dll                   # Upstream runtime DLLs
├── models/                      # Folder containing your .gguf models
│   ├── Llama-3.2-1B-Instruct.gguf
│   └── Qwen2.5-1.5B-Instruct.gguf
└── logs/                        # Auto-generated server session logs
```

---

## How to Get Binaries and Models

### 1. Upstream Binaries (`llama.cpp`)
Download the latest Windows prebuilt package from official releases:
- Go to [ggml-org/llama.cpp Releases](https://github.com/ggml-org/llama.cpp/releases).
- For modern Windows Intel/AMD machines, download `llama-bXXXX-bin-win-x64.zip` (or AVX2 / CUDA builds if you have an NVIDIA GPU).
- Extract `llama-server.exe` and its accompanying `.dll` files into this directory.

### 2. Recommended Offline Models (GGUF Format)
Small, fast models tuned for smooth CPU inference:
- **Llama 3.2 1B Instruct** (`IQ3_M` or `Q4_K_M`): ~700 MB - 1 GB (ultra-lightweight, rapid generation).
- **Qwen 2.5 1.5B Instruct** (`Q4_K_M`): ~1 GB (strong general reasoning and code capabilities).
- **Llama 3.2 3B Instruct** (`Q4_K_M`): ~2 GB (balanced quality on laptops with 8 GB+ RAM).

Download `.gguf` files directly from [Hugging Face](https://huggingface.co/models?search=gguf) and place them in the folder.

---

## Features

| Feature | Description |
| :--- | :--- |
| **Zero-Internet Architecture** | Designed for field, flight, and air-gapped use. Never stalls on disconnected networks. |
| **Single-Stroke Interactive TUI** | Instant keypress menu navigation (`msvcrt`) with rounded card framing and breadcrumbs. |
| **Direct Terminal Chat (`llama-cli`)** | Converse directly with local models in console without running a server or browser. |
| **Hardware Benchmark (`llama-bench`)** | Test prompt throughput and generation speed (tokens/sec) directly from the interface. |
| **Live Health & Telemetry Probing** | Real-time millisecond latency measurement against native `/health` and slot monitoring. |
| **In-Terminal REST API Ping** | Run live `/v1/chat/completions` test prompts directly in console to verify inference. |
| **Multimodal Projector Support** | Automatically categorizes and attaches vision projectors (`--mmproj`) for vision models. |
| **Mobile QR Pairing** | Generates an ASCII QR code in the terminal. No typing long IP addresses on phone browsers. |
| **Port Conflict Protection** | Dynamically audits socket states and assigns random open ports to prevent bind errors. |
| **Zombie Process Cleaner** | One-stroke force termination of orphaned background `llama-server` daemons. |
| **Profile Manager** | Save, load, and delete named launch profiles (e.g., `fast-1b`, `heavy-reasoning-7b`). |


---

## Connecting External Apps (OpenAI Compatible)

`llama-server` provides an OpenAI-compatible REST API. Once running, you can connect your favorite tools:

- **API Base URL**: `http://<LAN_IP>:<PORT>/v1`
- **Chat Endpoint**: `http://<LAN_IP>:<PORT>/v1/chat/completions`
- **Models Endpoint**: `http://<LAN_IP>:<PORT>/v1/models`

### Example: Python Integration

```python
from openai import OpenAI

# Connect to your Local AI Node over LAN
client = OpenAI(
    base_url="http://192.168.1.50:11708/v1",  # Replace with your LAN IP and Port
    api_key="not-needed"
)

response = client.chat.completions.create(
    model="local-model",
    messages=[{"role": "user", "content": "Explain quantum computing in two sentences."}]
)

print(response.choices[0].message.content)
```

### Compatible Frontends
- **Open WebUI**: Point connection to `http://<LAN_IP>:<PORT>/v1`
- **AnythingLLM / Jan / LibreChat**: Configure custom local OpenAI provider
- **Obsidian Copilot**: Connect notes to your private offline node
- **Mobile Browsers**: Native chat UI included directly at `http://<LAN_IP>:<PORT>/`

---

## Diagnostics Engine

Run Option `[4] Diagnostics` from the main menu to perform instant validation:
1. **Server Executable**: Verifies `llama-server.exe` is present in the workspace.
2. **Model Scanner**: Confirms valid `.gguf` weights exist on disk.
3. **Port Inspection**: Confirms port is clear and available for binding.
4. **Firewall Rule**: Audits Windows Firewall for incoming connection rules.
5. **Disk Space**: Ensures sufficient headroom for memory mapping and context swaps.
6. **LAN Address**: Resolves active local IP for network device routing.

---

## Optional Packages

The control center is completely functional with Python's built-in libraries. To enable terminal ASCII QR codes and system RAM profiling:

```bash
pip install -r requirements.txt
```

*(If these packages are absent, the script automatically falls back to plain URLs and skips memory profiling without failing).*

---

## Acknowledgments & Credits

- [llama.cpp](https://github.com/ggml-org/llama.cpp) by **Georgi Gerganov** and the open-source GGML community for the underlying inference technology.
- [LLVM Project](https://llvm.org/) for the OpenMP multi-threading runtime.

---

## License

This control center project is licensed under the [MIT License](LICENSE). Upstream `llama.cpp` and runtime components are subject to their respective open-source licenses.
