FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir markdown chromadb neo4j

COPY serve.py .

RUN mkdir -p /app/db /app/sessions

RUN useradd -m archiver && chown -R archiver:archiver /app && \
    mkdir -p /home/archiver/.cache/chroma && chown -R archiver:archiver /home/archiver/.cache
USER archiver

EXPOSE 8111

ENV DB_DIR=/app/db

CMD ["python", "-u", "serve.py", "--port", "8111"]
