FROM python:3.11-slim

LABEL maintainer="QA210"
LABEL description="Sweet-Strike Bank CTF Challenge"
LABEL challenge="Sweet-Strike Bank"

WORKDIR /app

# Install dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend
COPY backend/app.py .

# Copy frontend
COPY frontend/ ./frontend/

# Expose port
EXPOSE 31337

# Environment variables
ENV PORT=31337
ENV ADMIN_KEY=ssb-admin-2026
ENV PYTHONUNBUFFERED=1

# Run
CMD ["python", "app.py"]
