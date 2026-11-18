FROM python:3.12-slim

# Ghostscript does the fix, qpdf repairs/cleans, poppler-utils gives pdfinfo
RUN apt-get update \
 && apt-get install -y --no-install-recommends ghostscript qpdf poppler-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# Run as a normal user, not root
RUN useradd --create-home appuser && mkdir -p /srv/data && chown appuser /srv/data
USER appuser

ENV JOBS_DIR=/tmp/corelpdftools-jobs \
    DATA_DIR=/srv/data \
    PORT=8000
EXPOSE 8000

# Most hosts (Render, Railway) set $PORT themselves
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
