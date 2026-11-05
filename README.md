# Nova-Atlas

> **État Sprints 01 / 02 (novembre 2026)** : le socle actuel est Python/Flask,
> stockage JSON/YAML, Ollama CLI ou llama-server, Edge TTS/FFmpeg/Icecast.
> Les pages sont générées et servies par Flask ; la V2 n'est pas encore livrée.
> Sprint 01A ajoute une administration Flask fermée par défaut :
> [initialisation et connexion](docs/SECRETS_AND_ADMIN.md).
> Les descriptions ci-dessous restent historiques et ne garantissent pas la
> fiabilité ni la sécurité du déploiement. Les routes d'administration doivent
> rester sur un réseau maîtrisé ; les rendus et la CSP sont décrits dans
> [la sécurité web](docs/WEB_SECURITY.md).
> Sprint 01B centralise la [protection HTTP/SSRF](docs/HTTP_SECURITY.md) des sources.
> Sprint 01C fiabilise le [scheduler et son arrêt](docs/SCHEDULER.md) ;
> la radio respecte désormais les heures cibles de `config/bulletins.yaml`.
> Sprint 01D sécurise le [verrou LLM local](docs/LLM_LOCKING.md) ; tous les processus
> doivent être mis à jour ensemble lors d’un déploiement autorisé.
>
> [Vision V2](docs/VISION_V2.md) · [Architecture](docs/ARCHITECTURE_V2.md) ·
> [Roadmap V2](docs/ROADMAP_V2.md) · [Audit P0](docs/AUDIT_P0.md) ·
> [Baseline et tests hors ligne](docs/testing/BASELINE.md) ·
> [Secrets et routes admin](docs/SECRETS_AND_ADMIN.md).
> Baseline historique : 185 puis 190 tests au Sprint 00 ; 866 avant le Sprint 02.
> Les variables admin sont lues dans l’environnement du processus ; `.env` n’est pas chargé automatiquement.
> Incident historique OpenRouter signalé : révocation à réaliser par le propriétaire.


## Installation Compose préparée au Sprint 02

[Procédure complète : DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md).
Quatre services existants séparés (`web`, `news`, `radio`, `icecast`), profils
sélectifs, images versionnées/digests, utilisateur non-root, état POSIX local
partagé et secrets en fichiers. Web publié sur loopback ; Icecast reste interne.
Aucun GPU ni modèle dans les images. Aucun déploiement réel réalisé par cette PR.

```sh
python3.12 -m venv .venv-container
.venv-container/bin/python -m pip install -r requirements-runtime.lock
.venv-container/bin/python -m deploy.init --directory deploy/local --admin-username atlas-owner
docker compose --env-file deploy/local/compose.env --profile web config --quiet
docker compose --env-file deploy/local/compose.env --profile web build web
docker compose --env-file deploy/local/compose.env --profile web up -d --wait web
```

Le mot de passe est demandé sans écho ; initialisation limitée à un répertoire
neuf. Ce démarrage web seul n'active ni sources ni LLM ni radio. Configurer ensuite
HTTPS, le serveur LLM de confiance et les flux avant tout profil complet autorisé.
Arrêt, sauvegarde, restauration, permissions, limites et rollback sont documentés.
Ne jamais lancer la pile sur des données existantes par copie automatique.

Nova-Atlas is a fully autonomous AI-powered news engine and internet radio station.
It collects news from dozens of RSS feeds, analyzes them using local LLMs (Ollama or llama-server), and generates high-quality spoken bulletins, written editions, daily reports, social media posts, and a modern static website — all running locally.

---

## Features

### AI News Radio (24/7)
- Real-time detection of new articles
- Automatic generation of spoken radio bulletins (edge-tts + background music mixing)
- Continuous streaming to Icecast with a single permanent ffmpeg process
- Intelligent fade and intro music before each bulletin

### Rich Content Generation
- Daily Narrative Report at 23:00 — long-form journalistic summary
- Timed Editions : Morning (06:00), Midday (12:00), Evening (19:00)
- Social Media Posts: 4 optimized posts every 2 hours
- Live News Feed with category filtering and breaking news ticker

### Modern Web Experience
- Responsive static website with dark/light mode
- Integrated persistent radio player
- Archives of reports and editions
- Web-based configuration interface

---

## LLM Backends

Nova-Atlas supports two LLM backends, configurable in `config/config.yaml` under `llm.provider`:

### llama-server (recommended for production / GPU)
Uses the llama.cpp HTTP API. Better suited for dedicated GPU machines.
```yaml
llm:
  provider: "llama-server"
  model: "/path/to/model.gguf"
  base_url: "http://localhost:8080"   # or your GPU server's IP
  timeout_fetch: 240
  timeout_report: 600
  timeout_edition: 900
```

Start llama-server yourself:
```bash
llama-server -m /path/to/model.gguf --host 0.0.0.0 --port 8080 -c 8192 -t 16
```

Nova-Atlas can also auto-start llama-server if the binary is in your PATH.

### Ollama CLI (convenient for development)
Uses `ollama run` interactively. Fine for dev on a laptop CPU, but slower for production volumes.
```yaml
llm:
  provider: "ollama"
  model: "mistral:7b"
  base_url: "http://localhost:11434"
```

---

## Screenshots

### Web Interface (Live Feed + Radio Player)

![Nova-Atlas Web Interface](docs/screenshots/web-live-feed.png)
(Live news feed with category filters, breaking news banner, and integrated radio player)

### Console Output (News Engine + Radio)

![Nova-Atlas Console](docs/screenshots/console-output.png)
(Example of the rich console logging during operation)


---

## How It Works

Nova-Atlas is built around a modular architecture orchestrated by `main.py`.

### 1. News Engine (NewsEngine process)

- `atlas_fetch.py` periodically collects articles from a wide range of RSS sources (geopolitics, economy, tech, crypto, science, etc.).
- Content is extracted, deduplicated using hashes, and summarized using the configured LLM backend (Ollama CLI or llama-server HTTP).
- Articles are stored as daily JSON files in `data/articles/`.
- Additional tasks include generating social posts, daily reports, and timed editions on a precise schedule.

### 2. Radio Pipeline (Radio process)
- `news_watcher.py` continuously monitors the latest articles JSON and detects new valid summaries.
- When enough new articles are available, it triggers `journal_builder.py`:
  - Loads custom intros/transitions/outros from `messages.yaml`
  - Builds a natural-sounding script
  - Converts text to speech using **edge-tts** (random voice from configured list)
  - Mixes the voice with background music using **ffmpeg**
  - Places the finished MP3 in `audio_queue/`
  - `streamer.py` runs a single permanent ffmpeg process connected to Icecast:
  - Plays music from the `music/` library in a loop
  - Inserts bulletins from the queue with fade logic
  - Maintains a heartbeat with silence to keep the stream alive

### 3. Web Layer (WebServer process)

  - `atlas_web.py` generates a static website on demand (incremental or full rebuild).

- The site includes:
  - Live chronological feed with client-side filtering
  - Report and edition pages rendered from Markdown
  - Persistent radio player (iframe-based to survive navigation)
  - Configuration interface
- A lightweight Flask server serves the site in development mode.

### 4. Orchestration & Resilience
- `main.py` uses multiprocessing to run the three main services independently.
- Supports hot config reload (via flag file or `/config/restart` endpoint).
- Includes a watchdog that restarts crashed processes.
- LLM calls are protected by a smart file-based lock with priority system to avoid conflicts.

All components communicate through the filesystem (`data/`, `audio_queue/`, `site/`), making the system robust and easy to monitor.

---

## Project Structure

```bash
nova-atlas/
├── main.py
├── config/
│   ├── config.yaml
│   └── messages.yaml
├── data/
│   ├── articles/
│   ├── reports/
│   ├── editions/
│   ├── posts/
│   └── atlas_config.json
├── audio_queue/
├── background_music/
├── music/
├── site/                     # Generated static website
├── modules/
│   ├── core/                 # Config + LLM client (Ollama / llama-server)
│   ├── fetch/
│   ├── radio/                # NewsWatcher + JournalBuilder + Streamer
│   ├── report/
│   ├── editions/
│   ├── posts/
│   └── web/
└── requirements.txt
```

---

## Prerequisites

- Python 3.11+
- Ollama **or** llama-server installed and running
- Icecast server
- ffmpeg

### Ollama CLI
```bash
# Install
curl -fsSL https://ollama.com/install.sh | sh
# Pull a model
ollama pull mistral:7b
# Start server
ollama serve
```

### llama-server (llama.cpp)
```bash
# Build from source
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp/build
cmake .. -DLLAMA_BUILD_SERVER=ON -DLLAMA_ACCELERATE=on
cmake --build . --config Release
# Run
./bin/llama-server -m /path/to/model.gguf --host 0.0.0.0 --port 8080 -c 8192 -t 16
```

### Icecast
```bash
sudo apt install icecast2
# Edit /etc/icecast2/icecast.xml to set passwords
sudo service icecast2 start
```

### ffmpeg
```bash
sudo apt install ffmpeg
```

---

## Installation

```bash
git clone https://github.com/nikodindon/Nova-Atlas.git
cd Nova-Atlas
pip install -r requirements.txt
cp config/config.yaml.example config/config.yaml
```

> **Note:** `requirements.txt` installs all Python dependencies. For non-Python tools (ollama/llama-server, icecast, ffmpeg), see the Prerequisites section above.

Edit `config/config.yaml`:
- Set `llm.provider` to `ollama` or `llama-server`
- Set `llm.model` to your model name or GGUF path
- Set `llm.base_url` for llama-server (default: `http://localhost:8080`)
- Configure your language, Icecast credentials, and TTS voices

---

## Running the system

```bash
# Full stack
python main.py --all

# Individual components
python main.py --news
python main.py --radio
python main.py --web
```

See the Commands section below for one-shot operations.

---

## Available Commands

| Command | Description |
|---|---|
| `--all` | Start News Engine + Radio + Web |
| `--news` | News collection, editions, reports & posts |
| `--radio` | Radio streaming only |
| `--web` | Web server only |
| `--fetch` | Run one RSS fetch cycle |
| `--edition [matin\|midi\|soir]` | Generate a timed edition |
| `--report` | Generate daily narrative report |
| `--build` | Rebuild full static website |
| `--cleanup` | Remove invalid articles |

---

## Technology Stack

- **LLM**: Ollama (CLI) **or** llama-server (llama.cpp HTTP API)
- **TTS**: edge-tts
- **Audio Processing**: ffmpeg
- **Streaming**: Icecast
- **Web**: Flask + Jinja2 + custom modern CSS
- **Parsing**: BeautifulSoup4 + lxml + requests

---

## Roadmap

- [ ] Complete multi-language support for radio messages
- [ ] Improved cross-fade between music and bulletins
- [ ] Optional local TTS backend (e.g. Piper)
- [ ] Unified path management across all modules
- [ ] Docker / docker-compose support
- [ ] Enhanced admin dashboard

---

## License

MIT License

La passerelle LiteLLM est une extension **optionnelle**, utilisable avec un seul
modèle local et sans clé cloud. Voir [LLM_GATEWAY.md](docs/LLM_GATEWAY.md) pour les
alias, l'initialisation et le profil Compose gateway. Les backends historiques
restent disponibles ; aucun fournisseur payant n'est activé automatiquement.

Le socle PostgreSQL du Sprint 04 est **optionnel et limité aux snapshots** :
Alembic, import dry-run par défaut, vérification et export sans écrasement. News,
Web et Radio gardent leurs fichiers historiques ; aucune migration automatique.
Voir [DATABASE_MIGRATION.md](docs/DATABASE_MIGRATION.md) avant toute opération.

Le moteur d'ingestion prend désormais en charge RSS et Atom avec cache HTTP
conditionnel et état de santé privé, sans PostgreSQL obligatoire. Voir
[RSS_INGESTION.md](docs/RSS_INGESTION.md) pour formats, limites et configuration.
Après mise à jour d'une installation Python historique, réinstaller ses dépendances
pour disposer de feedparser épinglé ; les prompts et contrats publics restent inchangés.
