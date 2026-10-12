FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

# Nothing here needs root, so the services run as an ordinary user who owns /app.
RUN useradd --create-home attune && mkdir /app && chown attune /app
USER attune
WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY --chown=attune pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY --chown=attune alembic.ini ./
COPY --chown=attune alembic ./alembic
COPY --chown=attune app ./app

EXPOSE 8000

CMD ["sh", "-c", "uv run --no-dev alembic upgrade head && exec uv run --no-dev uvicorn app.main:app --host 0.0.0.0 --port 8000"]
