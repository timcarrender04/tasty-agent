++ b/backend_server/tasty-agent/Dockerfile
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package installer and resolver) system-wide
# Download and install uv binary directly
RUN curl -LsSf https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz | \
    tar -xz && \
    mv uv-x86_64-unknown-linux-gnu/uv /usr/local/bin/uv && \
    chmod +x /usr/local/bin/uv && \
    rm -rf uv-x86_64-unknown-linux-gnu
ENV PATH="/usr/local/bin:$PATH"

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create a non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose default port for HTTP server
EXPOSE 8033

# Default command (can be overridden in docker-compose)
# Note: Use uvicorn directly or the main() function
CMD ["python", "-c", "from tasty_agent.http_server import main; main()"]


