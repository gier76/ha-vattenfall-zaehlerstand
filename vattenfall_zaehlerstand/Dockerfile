# Playwright-Image bringt Python, Playwright und Chromium bereits mit.
# Es werden keine zusätzlichen pip-Pakete benötigt (HA-API und Webserver über die Standardbibliothek).
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# Das Playwright-Image hat kein tzdata -> ZoneInfo und ENV TZ funktionieren dort nicht.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Zeitplan-Meldungen in deutscher Lokalzeit (Container laufen sonst UTC).
ENV TZ=Europe/Berlin

WORKDIR /app
COPY run.py form.py ui.html /app/

EXPOSE 8099
CMD ["python3", "-u", "/app/run.py"]
