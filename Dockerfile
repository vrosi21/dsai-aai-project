# The production image: the code, its Python packages and the collected static files.
# Course content and the database are NOT in here; they are mounted in when it runs
# (deploy/compose.yaml), so one image serves any content.
#
# Two stages: the first has uv and builds everything, the second is what runs and receives
# only the finished /app folder.

# The official Python image, taken from Amazon's public mirror: Docker Hub limits
# anonymous downloads per address, and CI machines share theirs with everyone.
FROM public.ecr.aws/docker/library/python:3.13-slim AS build

# uv is a single binary; copy it from its official image at the version that wrote uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.12.16 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first: this layer is rebuilt only when the lock file changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY manage.py ./
COPY config config
COPY catalog catalog
COPY ingest ingest
COPY templates templates
COPY static static
COPY deploy/smoke.py deploy/smoke.py
# Shown only when DSAI_SHOW_EXAMPLE=1; the image smoke test in CI loads it.
COPY docs/example-course docs/example-course

# collectstatic needs settings but no database. The key is thrown away with this layer's
# environment; the real one is supplied when the container starts.
RUN DJANGO_DEBUG=0 DJANGO_SECRET_KEY=collectstatic-only DJANGO_ALLOWED_HOSTS=localhost \
    .venv/bin/python manage.py collectstatic --noinput


FROM public.ecr.aws/docker/library/python:3.13-slim

# Run as an ordinary user, not root. /data is the only place the app may write.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app \
    && mkdir -p /data/media /courses \
    && chown -R app:app /data

WORKDIR /app
COPY --from=build --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=0 \
    DSAI_DB_PATH=/data/db.sqlite3 \
    DSAI_MEDIA_ROOT=/data/media \
    DSAI_COURSES_DIR=/courses

USER app
VOLUME ["/data"]
EXPOSE 8000

# gunicorn is the WSGI server: it imports config/wsgi.py and runs Django in worker
# processes. Two workers with four threads each is plenty for a study site.
# The container's own files are read-only when it runs, so gunicorn keeps its scratch file
# in memory (/dev/shm) and does not create its control socket.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", "--threads", "4", \
     "--worker-tmp-dir", "/dev/shm", \
     "--no-control-socket", \
     "--access-logfile", "-", "--error-logfile", "-"]
