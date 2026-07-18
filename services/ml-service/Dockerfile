# Stage 1: Exporter
FROM python:3.12-slim AS exporter
WORKDIR /export

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Install PyTorch CPU and optimum/sentence-transformers for exporting
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir "optimum[onnxruntime]" sentence-transformers

COPY services/ml-service/export_onnx.py /export/export_onnx.py
ENV MODELS_DIR=/models
RUN python export_onnx.py

# Stage 2: Runtime (No PyTorch, No sentence-transformers)
FROM python:3.12-slim AS runtime
WORKDIR /app

# Copy the exported models
COPY --from=exporter /models /app/models

# Install runtime dependencies (onnxruntime, transformers, fastapi, numpy, etc.)
COPY services/ml-service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY services/ml-service/app/ /app/app/

ENV MODELS_DIR=/app/models
EXPOSE 8007
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8007"]
