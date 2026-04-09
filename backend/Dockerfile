# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

COPY pyproject.toml README.md ./

# Install third-party dependencies from pyproject first so application code
# changes don't invalidate the expensive dependency layer.
RUN python - <<'PY' > /tmp/requirements.txt
import tomllib

with open("pyproject.toml", "rb") as f:
    project = tomllib.load(f)["project"]

requirements = list(project.get("dependencies", []))
print("\n".join(requirements))
PY

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-cache-dir -r /tmp/requirements.txt

COPY . .

RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app

USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
