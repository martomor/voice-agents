FROM python:3.11-slim

# Install uv
RUN pip install uv

WORKDIR /app

# Copy dependency manifest first for layer caching
COPY pyproject.toml .

# Install dependencies with uv (no venv, install to system Python)
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy source
COPY src/ ./src/
COPY docs/ ./docs/

EXPOSE 8501

# FastAPI entrypoint — --reload watches for file changes (dev mode)
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8501", "--reload", "--reload-dir", "src"]
