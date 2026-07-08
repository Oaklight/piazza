# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12
ARG REGISTRY_MIRROR
FROM ${REGISTRY_MIRROR:-docker.io}/python:${PYTHON_VERSION}-alpine AS base

ARG PYPI_MIRROR
ARG LOCAL_WHEEL
ARG PACKAGE_VERSION

# Prevents Python from writing pyc files and enables unbuffered output.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Create a non-privileged user and group (alpine syntax).
RUN addgroup -g 1000 appgroup && \
    adduser -D -u 1000 -G appgroup appuser

# Install su-exec for runtime user switching in entrypoint.
RUN apk add --no-cache su-exec

# Copy only the dist/ directory for local wheels (never copy the full tree —
# even with .dockerignore, a broad COPY risks leaking secrets into layers).
# The trailing slash on dist/ + a wildcard ensures COPY is a no-op when empty.
RUN mkdir -p /tmp/dist/
COPY dist/ /tmp/dist/

# Install piazza: local wheel > specific version > latest from PyPI
# Then remove pip/setuptools to save space.
RUN if [ -n "$LOCAL_WHEEL" ] && [ -f "/tmp/dist/$LOCAL_WHEEL" ]; then \
        echo "Installing from local wheel: $LOCAL_WHEEL"; \
        if [ -n "$PYPI_MIRROR" ]; then \
            pip install -i "$PYPI_MIRROR" --no-cache-dir "/tmp/dist/${LOCAL_WHEEL}"; \
        else \
            pip install --no-cache-dir "/tmp/dist/${LOCAL_WHEEL}"; \
        fi; \
    elif [ -n "$PACKAGE_VERSION" ]; then \
        echo "Installing piazza version: $PACKAGE_VERSION"; \
        if [ -n "$PYPI_MIRROR" ]; then \
            pip install -i "$PYPI_MIRROR" --no-cache-dir "piazza==$PACKAGE_VERSION"; \
        else \
            pip install --no-cache-dir "piazza==$PACKAGE_VERSION"; \
        fi; \
    else \
        echo "Installing latest piazza from PyPI"; \
        if [ -n "$PYPI_MIRROR" ]; then \
            pip install -i "$PYPI_MIRROR" --no-cache-dir "piazza"; \
        else \
            pip install --no-cache-dir "piazza"; \
        fi; \
    fi && \
    pip uninstall -y pip setuptools 2>/dev/null || true && \
    rm -rf /tmp/dist/

# Data directory — mount a volume here for persistent SQLite storage.
RUN mkdir -p /data && chown appuser:appgroup /data
VOLUME /data

# Entrypoint fixes /data ownership and drops to appuser via su-exec.
# Set PUID/PGID environment variables to match your host user (default: 1000).
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8741 8742

ENTRYPOINT ["/entrypoint.sh"]
CMD ["piazza", "serve", "--db", "/data/piazza.db", "--http", "0.0.0.0:8742", "--remote", "--admin-port", "8741"]
