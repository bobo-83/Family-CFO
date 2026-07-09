# Model-manager sidecar (ADR 0013): python app + static docker CLI + compose
# plugin so it can run scripts/swap-model.sh against the mounted project.
FROM python:3.12-slim

ARG DOCKER_VERSION=27.3.1
ARG COMPOSE_VERSION=v2.32.1

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ARCH="$(uname -m)" \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${ARCH}/docker-${DOCKER_VERSION}.tgz" \
       | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    && case "$ARCH" in x86_64) CARCH=x86_64 ;; aarch64) CARCH=aarch64 ;; *) CARCH=$ARCH ;; esac \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${CARCH}" \
       -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "family_cfo_model_manager.main:app", "--host", "0.0.0.0", "--port", "8000"]
