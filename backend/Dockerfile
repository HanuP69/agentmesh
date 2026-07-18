FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /shared/
RUN pip install --no-cache-dir -e /shared

COPY backend/ /app/

EXPOSE 8089
CMD ["locust", "-f", "loadtest/locustfile.py", "--host", "https://nginx-edge"]
