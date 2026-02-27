FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir markdown chromadb neo4j

COPY serve.py .
COPY scripts/ ./scripts/

RUN mkdir -p /app/db /app/sessions /app/extractions_v2 /app/merged_raw /app/merged /app/backups

RUN useradd -m archiver && chown -R archiver:archiver /app && \
    mkdir -p /home/archiver/.cache/chroma && chown -R archiver:archiver /home/archiver/.cache
USER archiver

EXPOSE 8111

ENV DB_DIR=/app/db
ENV DATA_DIR=/app/archive

CMD ["python", "-u", "serve.py", "--port", "8111"]
