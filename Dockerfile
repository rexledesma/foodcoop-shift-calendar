# syntax = docker/dockerfile:1.2

FROM mcr.microsoft.com/playwright/python:v1.51.0-noble

COPY requirements.txt .

RUN pip install -r requirements.txt && playwright install chromium

COPY main.py .

RUN --mount=type=secret,id=_env,dst=/etc/secrets/.env cp /etc/secrets/.env .env
RUN --mount=type=secret,id=credentials_json,dst=/etc/secrets/credentials.json cp /etc/secrets/credentials.json credentials.json

CMD ["python", "main.py"]
