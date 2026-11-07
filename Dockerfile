# SuperAgent image (used by both the api and the worker container apps)
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[azure]" && useradd --uid 10001 --create-home agent
COPY config ./config
# demo product, copied into code-interpreter sessions when SA_PRODUCT_BOOTSTRAP_DIR points here
COPY sandbox/product/demo_app ./sandbox/product/demo_app
ENV SA_POLICY_FILE=/app/config/agent.yaml
USER 10001
EXPOSE 8000
CMD ["uvicorn", "superagent.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
