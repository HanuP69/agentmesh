#!/bin/bash
set -x
cd /home/claude/agentmesh/agentmesh-main
mkdir -p /tmp/agentmesh-run/logs

pkill -9 -f uvicorn 2>/dev/null
pkill -9 -f "node index.js" 2>/dev/null
pkill -9 -f hung_agent 2>/dev/null
sleep 1

redis-cli ping >/dev/null 2>&1 || redis-server --daemonize yes --port 6379 --save "" --appendonly no
sleep 1
redis-cli flushall >/dev/null

cd services/text-agent-service
VECTOR_BACKEND=memory USE_REDIS=true REDIS_URL=redis://127.0.0.1:6379/0 SYNTHESIZER_URL=http://127.0.0.1:8104 \
  setsid nohup python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8101 > /tmp/agentmesh-run/logs/text-agent.log 2>&1 < /dev/null &
cd ..

cd table-agent-service
VECTOR_BACKEND=memory USE_REDIS=true REDIS_URL=redis://127.0.0.1:6379/0 SYNTHESIZER_URL=http://127.0.0.1:8104 \
  setsid nohup python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8102 > /tmp/agentmesh-run/logs/table-agent.log 2>&1 < /dev/null &
cd ..

cd image-agent-service
VECTOR_BACKEND=memory USE_REDIS=true REDIS_URL=redis://127.0.0.1:6379/0 SYNTHESIZER_URL=http://127.0.0.1:8104 \
  setsid nohup python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8103 > /tmp/agentmesh-run/logs/image-agent.log 2>&1 < /dev/null &
cd ..

cd synthesizer-service
USE_REDIS=true REDIS_URL=redis://127.0.0.1:6379/0 LLM_PROVIDER=none \
  setsid nohup python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8104 > /tmp/agentmesh-run/logs/synthesizer.log 2>&1 < /dev/null &
cd ..

cd chat-service
setsid nohup python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8105 > /tmp/agentmesh-run/logs/chat.log 2>&1 < /dev/null &
cd ..

sleep 5

cd supervisor-service
TEXT_AGENT_URL=http://127.0.0.1:8101 TABLE_AGENT_URL=http://127.0.0.1:8102 IMAGE_AGENT_URL=http://127.0.0.1:8103 \
  SYNTHESIZER_URL=http://127.0.0.1:8104 CHAT_URL=http://127.0.0.1:8105 \
  REDIS_URL=redis://127.0.0.1:6379/0 USE_REDIS=true PORT=8010 \
  setsid nohup node index.js > /tmp/agentmesh-run/logs/supervisor.log 2>&1 < /dev/null &
cd ..

cd api-gateway
SUPERVISOR_URL=http://127.0.0.1:8010 CHAT_URL=http://127.0.0.1:8105 INGESTION_URL=http://127.0.0.1:8106 \
  REDIS_URL=redis://127.0.0.1:6379/0 USE_REDIS=true PORT=8000 JWT_SECRET=dev-insecure-secret-change-me \
  setsid nohup node index.js > /tmp/agentmesh-run/logs/gateway.log 2>&1 < /dev/null &
cd ..

sleep 3
set +x
echo "=== health checks ==="
for p in 8101 8102 8103 8104 8105 8010 8000; do
  echo -n "port $p: "
  curl -s -m 3 http://127.0.0.1:$p/health || echo "(no response)"
  echo
done
