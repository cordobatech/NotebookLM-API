# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A FastAPI service that automates Google NotebookLM via Playwright and Chrome DevTools Protocol (CDP). Provides REST endpoints for uploading sources, generating Audio Overviews, and downloading generated audio files.

## Common Commands

```bash
# Install dependencies
uv sync
uv run playwright install chromium

# Start the API server (requires NOTEBOOKLM_URL env var or --notebook-url flag)
uv run run-server
uv run run-server --notebook-url "https://notebooklm.google.com/notebook/<ID>"

# Run tests
uv run pytest tests/                    # all tests
uv run pytest tests/unit/               # unit tests only
uv run pytest -m "not slow" tests/      # skip slow tests
uv run pytest -m "not e2e" tests/       # skip e2e tests (requires NOTEBOOKLM_URL)
```

## Architecture

```
src/notebooklm_automator/
├── api/
│   ├── app.py          # FastAPI app with CORS, lifespan
│   ├── models.py       # Pydantic request/response models
│   └── routes.py       # REST endpoints, singleton automator instance
├── core/
│   ├── automator.py    # NotebookLMAutomator: main orchestrator class
│   ├── browser.py      # ChromeManager: CDP connection, auto-launch Chrome
│   ├── cookies.py      # Cookie/storageState parsing for auto-login
│   ├── selectors.py    # Localized UI selectors (en/he) for Playwright
│   ├── sources.py      # SourceManager: add/clear sources in notebook
│   └── audio.py        # AudioManager: generate audio, get status, download
└── main.py             # CLI entry point with argparse
```

### Key Patterns

- **Manager Pattern**: `SourceManager` and `AudioManager` encapsulate UI interactions for their domains
- **Singleton Automator**: `routes.py` uses a global `_automator_instance` to maintain browser state across API calls
- **Localization**: `selectors.py` provides `get_selector_by_language()` with fallback to English
- **CDP Auto-launch**: `ChromeManager.ensure_running()` will start Chrome if `NOTEBOOKLM_AUTO_LAUNCH_CHROME=1`
- **Dual Connection Mode**: Supports both CDP (local Chrome) and WebSocket (browserless) connections
- **StorageState Auth**: Auto-login via storage_state.json (preferred) or cookies.txt

### API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/sources/upload` | Upload URL/YouTube/text sources |
| POST | `/sources/clear` | Remove all sources |
| POST | `/audio/generate` | Start audio generation (returns job_id) |
| GET | `/audio/status/{job_id}` | Check generation status (includes title) |
| GET | `/audio/download/{job_id}` | Download audio binary |
| POST | `/studio/clear` | Delete all generated audio |
| POST | `/auth/save` | Save login state to storage_state.json |

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `NOTEBOOKLM_URL` | required | Notebook URL to automate |
| `NOTEBOOKLM_AUTO_LAUNCH_CHROME` | `1` | Auto-start Chrome with CDP |
| `NOTEBOOKLM_CHROME_PORT` | `9222` | CDP port |
| `NOTEBOOKLM_CHROME_USER_DATA_DIR` | `~/.notebooklm-chrome` | Chrome profile path |
| `NOTEBOOKLM_COOKIES_FILE` | - | Path to cookies.txt for auto-login |
| `NOTEBOOKLM_STORAGE_STATE` | - | Path to storage_state.json (preferred over cookies.txt) |
| `COOKIECLOUD_FILE` | - | Path to CookieCloud cookie.json file |
| `BROWSER_WS_ENDPOINT` | - | WebSocket endpoint for browserless (e.g., `ws://browserless:3000`) |

## Development Notes

- First run requires manual Google login in the Chrome profile, OR use cookie files for auto-login
- Auth injection priority: `storage_state.json` > `cookie.json` (CookieCloud) > `cookies.txt` > manual login
- After successful login, call `POST /auth/save` to save state for future sessions
- If Chrome user data dir already has login state, auth injection is skipped
- When using `BROWSER_WS_ENDPOINT`, auth is always injected (browserless is stateless)
- Source types: `url`, `youtube`, `text` (URL/YouTube are grouped and pasted together)
- Job IDs are 1-based indices into the `artifact-library` element

## Docker Deployment

The project supports lightweight Docker deployment using external browserless/chrome:

```bash
# Build and run with docker-compose
NOTEBOOKLM_URL="https://..." docker-compose up -d
```

The Dockerfile does not bundle Chrome - it connects to an external `browserless/chrome` container via WebSocket, keeping the image small (~200MB vs ~1GB+).
