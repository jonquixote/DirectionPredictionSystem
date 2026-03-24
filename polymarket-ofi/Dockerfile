FROM python:3.12-slim

WORKDIR /app

# System dependencies for building wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project code
COPY . .

# Default command
CMD ["python3", "-m", "pytest", "tests/", "-v"]
