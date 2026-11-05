# Der Playwright-Basis-Image bringt Python und die Chromium-Browser mit, aber
# NICHT das Python-Paket playwright (der Build-venv /ms-playwright-agent wird am
# Image-Ende entfernt). Die Wheels liegen daher im Add-on (vendor/) und werden
# ohne Netzwerk installiert: pip/apt rufen im Supervisor-Build auf diesem Host
# ab und hängen dort ewig. Die Browser liegen bereits unter
# PLAYWRIGHT_BROWSERS_PATH=/ms-playwright.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

COPY vendor/ /vendor/

# Wheels in Pythons Paketverzeichnis auspacken (stdlib zipfile, kein pip nötig).
# zipfile erhält keine Permissions -> der gebundelte node-driver wird ausführbar gemacht.
RUN python3 -c "import glob, zipfile; [zipfile.ZipFile(w).extractall('/usr/lib/python3/dist-packages') for w in glob.glob('/vendor/*.whl')]" \
    && chmod -R a+rX /usr/lib/python3/dist-packages/playwright /usr/lib/python3/dist-packages/greenlet /usr/lib/python3/dist-packages/pyee /usr/lib/python3/dist-packages/tzdata \
    && chmod 755 /usr/lib/python3/dist-packages/playwright/driver/node \
    && rm -rf /vendor

# Zeitplan-Meldungen in deutscher Lokalzeit: ZoneInfo nutzt das mitgelieferte
# tzdata-Paket (das Basis-Image hat keine System-tzdata).
ENV TZ=Europe/Berlin

WORKDIR /app
COPY run.py form.py ui.html /app/

EXPOSE 8099
CMD ["python3", "-u", "/app/run.py"]
