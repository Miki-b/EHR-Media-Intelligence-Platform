# Single-container demo image: builds the UI, runs the whole pipeline on the synthetic
# dataset (mock LLM + committed summary cache, so no API key is needed), downloads the
# embedding model, and serves UI + API from FastAPI on one port.
#
#   docker build -t ehr-demo .
#   docker run -p 7860:7860 ehr-demo        # http://localhost:7860
#
# Hugging Face Spaces (Docker SDK) builds this as-is; port 7860 is their default.

# ---- 1. frontend -------------------------------------------------------------
FROM node:20-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Same-origin deploy: the UI calls /search, /patients directly (no /api proxy).
ENV VITE_API_BASE=""
RUN npm run build

# ---- 2. backend --------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface TOKENIZERS_PARALLELISM=false \
    LLM_PROVIDER=mock API_HOST=0.0.0.0 API_PORT=7860
WORKDIR /app

# CPU-only torch keeps the image a few GB smaller than the default CUDA wheels.
COPY pyproject.toml README.md ./
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu torch \
 && mkdir -p app && touch app/__init__.py \
 && pip install -e .

COPY app/ app/
COPY scripts/ scripts/
COPY eval/ eval/
COPY data/ data/
COPY docs/ docs/
COPY --from=ui /ui/dist frontend/dist

# Build the dataset and the index into the image so the first request is instant.
RUN python scripts/generate_data.py \
 && python -m app.pipeline all \
 && python -c "from app.search.embed import get_embedder; get_embedder().encode(['warm'])"

# Spaces run the container as uid 1000; Chroma and SQLite need write access.
RUN useradd -m -u 1000 app && chown -R app:app /app
USER app

EXPOSE 7860
CMD ["python", "-m", "uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "7860"]
