FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gosu tesseract-ocr tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY scripts/container-entrypoint.sh /usr/local/bin/coastwise-entrypoint
RUN uv sync --frozen --no-dev
RUN useradd --uid 10001 --user-group --create-home appuser && mkdir -p /app/.local/runs && chown -R appuser:appuser /app && chmod 755 /usr/local/bin/coastwise-entrypoint
USER appuser
ENV COASTWISE_DATA_DIR=/app/.local/runs
ENV UV_CACHE_DIR=/tmp/coastwise-uv-cache
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/coastwise-entrypoint"]
CMD ["sh", "-c", "exec uv run --no-sync coastwise serve --host 0.0.0.0 --port \"${PORT:-8080}\""]
