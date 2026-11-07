# Optional custom sandbox image for ACA dynamic sessions (custom container pool) and local Docker.
# Contains common L2/L3 troubleshooting tools. Runs as an unprivileged user.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl wget dnsutils iputils-ping traceroute netcat-openbsd openssl ca-certificates jq \
      ldap-utils krb5-user smbclient procps less vim-tiny git unzip \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pyyaml requests cryptography msal pandas \
    && useradd --uid 10001 --create-home sandbox
COPY runner.py /opt/runner.py
USER 10001
WORKDIR /home/sandbox
EXPOSE 8080
CMD ["python", "/opt/runner.py"]
