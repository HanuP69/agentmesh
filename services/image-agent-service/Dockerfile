FROM python:3.12-slim

WORKDIR /app

# Install shared library first (cached layer)
COPY shared/ /app/shared/
RUN pip install --no-cache-dir -e /app/shared

# Install service-specific deps
COPY services/image-agent-service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy service code
COPY services/image-agent-service/app/ /app/app/

EXPOSE 8003
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
