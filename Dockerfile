FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Expose server port
EXPOSE 5000

ENV HOST=0.0.0.0
ENV PORT=5000
ENV DEBUG=False

# Run with Gunicorn production server
CMD ["gunicorn", "server:app", "--bind", "0.0.0.0:5000", "--workers", "3", "--timeout", "120"]
