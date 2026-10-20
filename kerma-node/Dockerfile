FROM python:3.11-slim

WORKDIR /app

COPY . /app

# Directory for persistent data (mounted as a volume by docker-compose)
RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1

EXPOSE 18018

CMD ["python", "main.py"]