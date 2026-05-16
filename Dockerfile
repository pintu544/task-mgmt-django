# syntax=docker/dockerfile:1

# Single-stage image for the Django overdue service.
#
# Built on python:3.11-slim to keep the image small while still giving us
# a glibc base that mysqlclient compiles against cleanly. Gunicorn is the
# production WSGI server; the container honors $PORT so the same image
# runs on Render, Railway, Fly.io and plain `docker run`.
FROM python:3.11-slim

# Build deps for mysqlclient (kept in the final image because slim does
# not have a separate stage and mysqlclient links against libmysqlclient
# at runtime). For a smaller image, switch to PyMySQL and drop the
# default-libmysqlclient-dev / build-essential packages.
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
        default-libmysqlclient-dev \
        pkg-config \
        build-essential \
        curl \
  && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python deps first so the layer is cached across source edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source.
COPY . .

# Collect static files. The overdue service has no public assets, but
# running collectstatic ensures the admin's CSS is served if the admin
# URL is ever opened during debugging.
RUN python manage.py collectstatic --noinput || true

ENV PORT=8001
EXPOSE 8001

# 2 workers is sufficient for the overdue service's traffic profile
# (cron-driven sweeps + occasional close requests). Tune via $WEB_CONCURRENCY
# at deploy time when needed.
CMD ["sh", "-c", "gunicorn task_mgmt_django.wsgi:application --bind 0.0.0.0:${PORT:-8001} --workers ${WEB_CONCURRENCY:-2} --access-logfile -"]
