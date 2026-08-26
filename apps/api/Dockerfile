FROM python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CONNECTMD_STORAGE_PATH=/app/storage

WORKDIR /app
ARG DEBIAN_SNAPSHOT=20260805T010740Z
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
    && printf '%s\n' \
      'Types: deb' \
      "URIs: https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}" \
      'Suites: trixie' \
      'Components: main' \
      'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
      'Check-Valid-Until: no' \
      > /etc/apt/sources.list.d/connectmd-snapshot.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
      libmagic1t64=1:5.46-5 \
      poppler-utils=25.03.0-5+deb13u4 \
      tesseract-ocr=5.5.0-1+b1 \
    && test "$(dpkg-query -W -f='${Package}=${Version}\n' \
      libmagic1t64 poppler-utils tesseract-ocr | LC_ALL=C sort)" = \
      "$(printf '%s\n' \
        'libmagic1t64=1:5.46-5' \
        'poppler-utils=25.03.0-5+deb13u4' \
        'tesseract-ocr=5.5.0-1+b1' | LC_ALL=C sort)" \
    && rm -rf /var/lib/apt/lists/*
COPY apps/api/pyproject.toml apps/api/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY apps/api/app ./app
COPY apps/api/alembic.ini ./
COPY apps/api/alembic ./alembic
COPY packages/markdown-schemas /opt/connectmd/packages/markdown-schemas
ENV CONNECTMD_SCHEMA_PATH=/opt/connectmd/packages/markdown-schemas/schemas
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
RUN pip check

RUN addgroup --system --gid 10001 connectmd \
    && adduser --system --uid 10001 --ingroup connectmd --home /nonexistent connectmd \
    && mkdir -p /app/storage /ingest-jobs \
    && chown -R connectmd:connectmd /app /ingest-jobs
USER connectmd

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
