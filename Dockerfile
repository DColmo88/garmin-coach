FROM python:3.11-slim

WORKDIR /app

# Dipendenze (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codice
COPY app ./app

# I dati (DB + token) vivono in un volume montato su ./data
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
