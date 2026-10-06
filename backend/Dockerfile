FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        fontconfig \
        bubblewrap \
        fonts-noto-cjk \
        libreoffice-impress \
        nodejs \
        npm \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-chi-tra \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Decks declare Microsoft JhengHei so they look right on Windows. The image has
# no Microsoft fonts, so every render in the container (the author's previews
# and the backend's final render) substitutes Noto Sans CJK TC for it.
RUN printf '%s\n' \
        '<?xml version="1.0"?>' \
        '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">' \
        '<fontconfig>' \
        '  <alias binding="same"><family>Microsoft JhengHei</family><accept><family>Noto Sans CJK TC</family></accept></alias>' \
        '  <alias binding="same"><family>微軟正黑體</family><accept><family>Noto Sans CJK TC</family></accept></alias>' \
        '</fontconfig>' \
        > /etc/fonts/conf.d/60-delivery-font.conf \
    && fc-cache -f

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}" \
    NODE_PATH=/app/node_modules

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
    && npm cache clean --force

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY ./app ./app
COPY ./.agents ./.agents
COPY ./config.yaml ./config.yaml
COPY ./alembic.ini ./alembic.ini
COPY ./alembic ./alembic
COPY ./scripts ./scripts

CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
