# syntax=docker/dockerfile:1.7
FROM postgres:16-bookworm AS postgres_tools
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN --mount=type=secret,id=build_ca,target=/run/secrets/build-ca.pem,mode=0444,required=false \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && if [ -f /run/secrets/build-ca.pem ]; then export APT_CA_OPTION='Acquire::https::CaInfo=/run/secrets/build-ca.pem'; else export APT_CA_OPTION='Acquire::https::Verify-Peer=true'; fi \
    && apt-get -o "$APT_CA_OPTION" update \
    && apt-get -o "$APT_CA_OPTION" install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home-dir /app app
COPY requirements.txt /app/requirements.txt
COPY --from=postgres_tools /usr/lib/postgresql/16/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=postgres_tools /usr/lib/postgresql/16/bin/pg_restore /usr/local/bin/pg_restore
RUN --mount=type=secret,id=build_ca,target=/run/secrets/build-ca.pem,mode=0444,required=false \
    if [ -f /run/secrets/build-ca.pem ]; then export PIP_CERT=/run/secrets/build-ca.pem; fi \
    && python -m pip install --disable-pip-version-check -r requirements.txt
COPY --chown=app:app . /app
RUN mkdir -p /app/.local && chown app:app /app/.local \
    && chmod +x /app/deployment/entrypoint.sh
USER app
EXPOSE 8000
ENTRYPOINT ["/app/deployment/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind=0.0.0.0:8000", "--workers=2", "--timeout=60", "--access-logfile=-", "--error-logfile=-"]
