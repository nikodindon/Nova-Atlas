# Nova-Atlas — Project Context

## Project Overview

**Nova-Atlas** is a fully autonomous AI-powered news engine and internet radio station. It collects news from RSS feeds, analyzes articles using local LLMs (Ollama), and generates spoken radio bulletins, written editions, daily reports, social media posts, and a modern static website — all running locally.

### Core Features

- **24/7 AI News Radio**: Real-time article detection, automatic bulletin generation (edge-tts + background music mixing), continuous Icecast streaming with fade logic
- **Rich Content Generation**: Daily narrative reports at 23:00, timed editions (06:00, 12:00, 19:00), social media posts every 2 hours
- **Modern Static Website**: Responsive site with dark/light mode, integrated radio player, archives, and web-based config interface

## Architecture

The system uses a modular multiprocessing architecture orchestrated by `main.py`:

| Component | Module | Description |
|-----------|--------|-------------|
| **News Engine** | `modules.fetch.atlas_fetch` | RSS collection, deduplication, LLM summarization |
| **Report Gen** | `modules.report.atlas_report` | Daily long-form narrative reports |
| **Edition Gen** | `modules.editions.atlas_editions` | Morning/midday/evening news editions |
| **Post Gen** | `modules.posts.atlas_posts` | Social media post generation |
| **Radio** | `modules.radio.*` | NewsWatcher, JournalBuilder, Streamer |
| **Web** | `modules.web.atlas_web` | Static site generation + Flask dev server |
| **Core** | `modules.core.*` | Config loading, Ollama client management |

### Data Flow

All components communicate through the filesystem:
- `data/articles/` — Daily JSON article files
- `data/reports/` — Generated daily reports (Markdown)
- `data/editions/` — Timed editions (Markdown)
- `data/posts/` — Social media posts
- `audio_queue/` — Generated MP3 bulletins waiting for streaming
- `site/` — Generated static website
- `music/` — Music library for radio background

## Technology Stack

| Layer | Technology |
|-------|------------|
| **LLM** | Ollama (local models, default: `mistral:7b`) |
| **TTS** | edge-tts (multiple French voices configured) |
| **Audio** | ffmpeg (mixing, fading, encoding) |
| **Streaming** | Icecast (via `docker-compose.yml`) |
| **Web** | Flask + Jinja2 + custom CSS |
| **Parsing** | BeautifulSoup4 + lxml + requests |
| **Config** | PyYAML (`config/config.yaml`, `config/messages.yaml`) |

## Building and Running

### Prerequisites

- Python 3.11+
- Ollama (running locally)
- Icecast server (Docker Compose provided)
- ffmpeg

### Setup

```bash
pip install -r requirements.txt
```

### Running the System

```bash
# Full stack (News Engine + Radio + Web)
python main.py --all

# Individual components
python main.py --news    # News engine only
python main.py --radio   # Radio streaming only
python main.py --web     # Web server only
```

### One-Shot Commands

```bash
python main.py --fetch              # Run one RSS fetch cycle
python main.py --build              # Rebuild full static website
python main.py --edition [matin|midi|soir|auto]   # Generate an edition
python main.py --report [YYYYMMDD]  # Generate daily report
python main.py --cleanup            # Remove invalid articles
```

### Icecast (Docker)

```bash
docker compose up -d
```

Default credentials: password `hackme`, mount `/nova`, port `8000`.

### Key Configuration

- **Config file**: `config/config.yaml`
- **Radio messages**: `config/messages.yaml` (intros, transitions, outros)
- **Web server**: Port 5055 by default
- **Service name**: "Nikodindondes"
- **Default language**: French (`fr`)

## Project Structure

```
nova-atlas/
├── main.py                    # Entry point + orchestrator
├── config/
│   ├── config.yaml            # Main configuration
│   └── messages.yaml          # Radio intros/transitions/outros
├── data/
│   ├── articles/              # Daily article JSON files
│   ├── reports/               # Generated daily reports
│   ├── editions/              # Timed news editions
│   └── posts/                 # Social media posts
├── audio_queue/               # Bulletin MP3 queue
├── background_music/          # Background music tracks
├── music/                     # Music library for streaming
├── site/                      # Generated static website
├── modules/
│   ├── core/                  # Config + Ollama client
│   ├── fetch/                 # RSS fetching
│   ├── radio/                 # NewsWatcher, JournalBuilder, Streamer
│   ├── report/                # Daily report generation
│   ├── editions/              # Timed edition generation
│   ├── posts/                 # Social media post generation
│   ├── web/                   # Static site + Flask server
│   └── utils/                 # Shared utilities
├── scripts/                   # (empty, reserved for scripts)
├── docker-compose.yml         # Icecast container
├── icecast.xml                # Icecast configuration
├── requirements.txt           # Python dependencies
└── README.md                  # Project documentation
```

## Development Conventions

- **Language**: Python 3.11+, French comments/docstrings in source code
- **Logging**: Structured logging to both console and `nova.log`
- **Config**: YAML-based with hot-reload support via flag file (`data/.reload_config`)
- **Process Management**: Multiprocessing with watchdog for crashed process recovery
- **Ollama**: File-based locking with priority system to prevent conflicts
- **Windows Support**: Uses `multiprocessing.freeze_support()` for Windows compatibility

## Scheduling Summary

| Task | Frequency | Time |
|------|-----------|------|
| RSS Fetch | Every 30 min | 05:00–23:59 |
| Social Posts | Configurable hours | Default: 7, 9, 11, 13, 15, 17, 19, 21 |
| Editions | 3x daily | 06:00, 12:00, 19:00 |
| Daily Report | Once daily | 23:00 |
| Site Rebuild | Once daily | 23:30 |
