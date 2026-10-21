# Backend image with the embedding model baked in, so workers start without any download.
# Build from the repository root:  docker build -f backend/Dockerfile -t legacylift-backend .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    EMBEDDING_CACHE_DIR=/opt/models HF_HOME=/opt/models HF_HUB_OFFLINE=0

RUN pip install uv && groupadd --gid 10002 app && useradd --uid 10002 --gid 10002 --create-home app

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./

# Install exactly the locked versions. PyTorch comes from the CPU index: no GPU libraries in the image.
RUN uv export --frozen --no-dev --no-hashes --no-emit-project -o /tmp/requirements.txt \
    && grep -viE '^(nvidia-|triton|cuda-)' /tmp/requirements.txt > /tmp/requirements-cpu.txt \
    && pip install --no-deps --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple -r /tmp/requirements-cpu.txt

COPY backend/ ./
COPY datasets/targets /app/datasets/targets
RUN mkdir -p /opt/models && python -m app.mapping.download_model && chown -R app:app /opt/models /app

ENV HF_HUB_OFFLINE=1
USER 10002:10002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
