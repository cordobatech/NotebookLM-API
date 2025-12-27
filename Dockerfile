# Lightweight Dockerfile for NotebookLM Automator
# Uses external browserless/chrome container, no Chrome bundled

FROM python:3.11-slim

WORKDIR /app

# Install minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Install dependencies (no Playwright browsers needed when using browserless)
RUN uv sync --frozen --no-dev

# Create cookies directory
RUN mkdir -p /app/local/cookies

# Expose API port
EXPOSE 8000

# Default environment variables
ENV PYTHONUNBUFFERED=1
ENV NOTEBOOKLM_AUTO_LAUNCH_CHROME=0

# Run the API server
CMD ["uv", "run", "run-server", "--host", "0.0.0.0"]
