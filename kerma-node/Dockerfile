FROM python:3.11-slim

WORKDIR /app

# Install dependencies at build time (the node has no network access during grading)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Directory for persistent data (mounted as a volume by docker-compose)
RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1

EXPOSE 18018

CMD ["python", "main.py"]
