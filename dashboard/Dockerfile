FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Non-root user for security
# /config is pre-created here so the named volume is initialised with the
# correct ownership when Docker mounts it for the first time.
RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app \
    && mkdir -p /config && chown appuser:appuser /config
USER appuser

EXPOSE 5000

CMD ["python", "app.py"]
