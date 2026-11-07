# Product sandbox: the product under test + troubleshooting tools + a headless browser + the exec API.
# Used as an ACA dynamic-sessions custom-container pool (target "product") or as a local Docker image.
#
#   az acr build -r <registry> -t superagent-product:<tag> -f sandbox/product/Dockerfile sandbox/
#   docker build -t superagent-product:latest -f sandbox/product/Dockerfile sandbox/
#
# To use your own product: install it below (or start FROM its image and add the layers here),
# then set PRODUCT_START_CMD to the command that starts it. Keep test data only: no production data.
# bookworm: Playwright's --with-deps installer supports Debian 12
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl wget dnsutils iputils-ping netcat-openbsd openssl ca-certificates jq procps less vim-tiny git unzip sqlite3 \
    && pip install playwright==1.49.1 \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home sandbox
COPY runner.py /opt/runner.py
COPY product/browse.py /usr/local/bin/browse
COPY product/start.sh /opt/start.sh
COPY product/demo_app /opt/product
RUN chmod 0755 /usr/local/bin/browse /opt/start.sh
ENV PRODUCT_START_CMD="python /opt/product/app.py" PORT=3000
USER 10001
WORKDIR /home/sandbox
EXPOSE 8080
CMD ["/opt/start.sh"]
