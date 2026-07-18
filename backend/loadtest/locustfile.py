"""Load test for AgentMesh. Hits the mix of endpoints a real dashboard user
would: mostly /query, occasional /status polling (SSE substitute), rare
/ingest.

Run:
    pip install locust
    locust -f loadtest/locustfile.py --host http://localhost:8000

Then open http://localhost:8089, set concurrency/spawn-rate, start.
Headless (for CI / capturing numbers):
    locust -f loadtest/locustfile.py --host http://localhost:8000 \
        --headless -u 50 -r 5 -t 60s --csv=loadtest/results
"""
import random
import time
import uuid
import jwt

from locust import HttpUser, between, task

QUERIES = [
    "how tall is the eiffel tower",
    "compare revenue numbers in the table",
    "what does the architecture diagram show",
    "fastest animal on land",
    "explain consistent hashing",
]


class AgentMeshUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self):
        self.client.verify = False
        # Generate unique JWT token to simulate distinct authenticated users
        token_payload = {
            "sub": f"user-{uuid.uuid4()}",
            "email": f"locust-{uuid.uuid4()}@example.com",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600
        }
        secret = "dev-insecure-secret-change-me"
        self.token = jwt.encode(token_payload, secret, algorithm="HS256")
        self.headers = {"Authorization": f"Bearer {self.token}"}

    @task(6)
    def query(self):
        self.client.post(
            "/query",
            json={"query": random.choice(QUERIES), "top_k": 5, "urgency": 1.0},
            headers=self.headers,
            name="/query",
        )

    @task(2)
    def status(self):
        self.client.get("/status", headers=self.headers, name="/status")

    @task(1)
    def health(self):
        self.client.get("/health", headers=self.headers, name="/health")
