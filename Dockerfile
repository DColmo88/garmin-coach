FROM python:3.11-slim

WORKDIR /app

# psycopg ha bisogno delle librerie client di Postgres.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Dipendenze prima del codice: il layer resta in cache fra i deploy.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .

# I dati (token Garmin) vivono nel volume montato su ./data
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# L'utente non-root non deve poter scrivere nel codice, solo nei dati.
RUN useradd --create-home --uid 1000 garmin \
    && mkdir -p /app/data \
    && chown -R garmin:garmin /app/data
USER garmin

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
