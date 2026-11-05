# Playwright-Image bringt Python und Chromium-Browser mit, aber NICHT das
# Python-Paket playwright (der Build-venv /ms-playwright-agent wird am Image-
# Ende entfernt). Das Paket wird hier nachinstalliert; die Browser liegen
# bereits unter PLAYWRIGHT_BROWSERS_PATH=/ms-playwright.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

RUN pip3 install --no-cache-dir playwright==1.48.0

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
