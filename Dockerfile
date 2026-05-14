FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY consuelo ./consuelo
COPY prompts ./prompts

RUN pip install --no-cache-dir .

ENV VAULT_PATH=/vault \
    STATE_PATH=/state/state \
    CACHE_PATH=/state/cache \
    CHROMA_PATH=/state/chroma

ENTRYPOINT ["consuelo"]
CMD ["run"]
