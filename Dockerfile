FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

USER appuser

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--preload", "app:app"]
