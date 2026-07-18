#Requires -Version 5.1
<#
  AgentMesh -- run_benchmarks.ps1

  One-shot benchmark runner. Assumes this script sits in the repo root
  (next to backend/ and frontend/). Run from PowerShell:

      cd agentmesh
      .\run_benchmarks.ps1

  What it does:
    1. Checks Python + Ollama are reachable, warns (doesn't fail) if not.
    2. Installs backend deps (+ loadtest deps).
    3. Runs benchmarks/testbench.py       -> component-level numbers
    4. Runs benchmarks/openrag_bench.py   -> multimodal retrieval numbers
    5. Starts the API server in the background, runs autocannon (if npx/node
       available) and a headless Locust run against it, then stops it.
    6. Writes everything to .\benchmark-results\<timestamp>\ as .txt files
       plus a combined summary.md you can lift resume bullets from.

  All steps are best-effort: one failing step prints a warning and the
  script continues, so you still get whatever numbers succeeded.
#>

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$resultsDir = Join-Path $root "benchmark-results\$timestamp"
New-Item -ItemType Directory -Path $resultsDir -Force | Out-Null

function Write-Section($title) {
    Write-Host ""
    Write-Host "==== $title ====" -ForegroundColor Cyan
}

function Run-Step {
    param([string]$Name, [scriptblock]$Body, [string]$OutFile)
    Write-Section $Name
    try {
        & $Body 2>&1 | Tee-Object -FilePath (Join-Path $resultsDir $OutFile)
        Write-Host "OK: $Name" -ForegroundColor Green
    } catch {
        Write-Host "FAILED: $Name -- $($_.Exception.Message)" -ForegroundColor Yellow
        "FAILED: $($_.Exception.Message)" | Out-File (Join-Path $resultsDir $OutFile) -Append
    }
}

# ---------------------------------------------------------------------------
# 0. Preflight
# ---------------------------------------------------------------------------
Write-Section "Preflight checks"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) {
    Write-Host "Python not found on PATH. Install Python 3.10+ and re-run." -ForegroundColor Red
    exit 1
}
$pythonExe = $python.Source
Write-Host "Python: $pythonExe"

$ollamaUp = $false
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 2 -UseBasicParsing
    if ($resp.StatusCode -eq 200) { $ollamaUp = $true }
} catch {}
if ($ollamaUp) {
    Write-Host "Ollama: reachable at localhost:11434" -ForegroundColor Green
} else {
    Write-Host "Ollama: NOT reachable. Run 'ollama serve' in another terminal for real embedding numbers." -ForegroundColor Yellow
    Write-Host "        (script continues -- you'll get hash-fallback numbers instead, clearly labeled)" -ForegroundColor Yellow
}

$npx = Get-Command npx -ErrorAction SilentlyContinue
if ($npx) { Write-Host "Node/npx: found (autocannon step will run)" -ForegroundColor Green }
else { Write-Host "Node/npx: not found (autocannon step will be skipped)" -ForegroundColor Yellow }

$redisUp = $false
try {
    $tcp = Test-NetConnection -ComputerName localhost -Port 6379 -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($tcp) { $redisUp = $true }
} catch {}

$startedRedisContainer = $false
if ($redisUp) {
    Write-Host "Redis: already reachable at localhost:6379" -ForegroundColor Green
} else {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) {
        Write-Host "Redis: not running -- starting a temporary container (redis:7-alpine)..." -ForegroundColor Yellow
        docker rm -f agentmesh-redis-bench 2>$null | Out-Null
        docker run -d --name agentmesh-redis-bench -p 6379:6379 redis:7-alpine 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        try {
            $tcp = Test-NetConnection -ComputerName localhost -Port 6379 -WarningAction SilentlyContinue -InformationLevel Quiet
            if ($tcp) { $redisUp = $true; $startedRedisContainer = $true; Write-Host "Redis: container up" -ForegroundColor Green }
        } catch {}
    }
    if (-not $redisUp) {
        Write-Host "Redis: NOT reachable and could not auto-start (no Docker found, or it failed)." -ForegroundColor Yellow
        Write-Host "        Rate limiter/priority queue benchmarks will use the in-memory fallback instead" -ForegroundColor Yellow
        Write-Host "        of real Redis WATCH/MULTI/EXEC and sorted-set code paths -- clearly labeled in output." -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# 1. Install deps
# ---------------------------------------------------------------------------
Run-Step "Installing backend dependencies (required)" {
    Push-Location $backend
    & $pythonExe -m pip install -q -r requirements.txt
    Pop-Location
} "00_pip_install.txt"

$locustAvailable = $false
Write-Section "Installing Locust (optional -- skipped gracefully if it fails)"
try {
    Push-Location $backend
    # --only-binary avoids gevent trying to compile from source (needs a C
    # toolchain on Windows and commonly fails on newer Python versions where
    # no prebuilt wheel exists yet). If even the wheel isn't available for
    # your Python version, this step is skipped -- autocannon still covers
    # HTTP-level load numbers.
    & $pythonExe -m pip install -q --only-binary :all: -r loadtest\requirements.txt 2>&1 |
        Out-File (Join-Path $resultsDir "00b_pip_install_loadtest.txt")
    & $pythonExe -c "import locust" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $locustAvailable = $true
        Write-Host "Locust: installed OK" -ForegroundColor Green
    } else {
        Write-Host "Locust: not available for this Python version -- skipping Locust step (autocannon still runs)" -ForegroundColor Yellow
    }
    Pop-Location
} catch {
    Write-Host "Locust: install failed -- skipping Locust step (autocannon still runs)" -ForegroundColor Yellow
    Pop-Location
}

# ---------------------------------------------------------------------------
# 2. Component-level testbench (hashing, rate limiter, circuit breaker,
#    cache, queue, retrieval, contradiction detection, latency)
# ---------------------------------------------------------------------------
Run-Step "Component test bench (backend\benchmarks\testbench.py)" {
    Push-Location $backend
    $env:LLM_PROVIDER = "ollama"
    # testbench.py auto-detects Redis (localhost:6379) and Ollama itself and
    # prints which backend each benchmark actually used -- USE_REDIS is not
    # read by this script, only by main.py's server (set below for that).
    & $pythonExe -m benchmarks.testbench
    Pop-Location
} "01_testbench.txt"

Copy-Item (Join-Path $backend "benchmarks\report.json") (Join-Path $resultsDir "01_testbench_report.json") -ErrorAction SilentlyContinue

# ---------------------------------------------------------------------------
# 3. Open RAG Benchmark (multimodal retrieval, real dataset if you've
#    downloaded it to data\openragbench, else the bundled fixture)
# ---------------------------------------------------------------------------
$openragData = Join-Path $root "data\openragbench"
Run-Step "Open RAG Benchmark (backend\benchmarks\openrag_bench.py)" {
    Push-Location $backend
    $env:LLM_PROVIDER = "ollama"
    if (Test-Path $openragData) {
        & $pythonExe -m benchmarks.openrag_bench --data-dir $openragData --limit 300
    } else {
        Write-Host "No dataset at $openragData -- running bundled 3-paper fixture instead." -ForegroundColor Yellow
        Write-Host "  (to use the real dataset: pip install huggingface_hub; hf download vectara/open_ragbench --repo-type dataset --local-dir data\openragbench)" -ForegroundColor Yellow
        & $pythonExe -m benchmarks.openrag_bench
    }
    Pop-Location
} "02_openrag_bench.txt"

Copy-Item (Join-Path $backend "benchmarks\openrag_report.json") (Join-Path $resultsDir "02_openrag_report.json") -ErrorAction SilentlyContinue

# ---------------------------------------------------------------------------
# 4. Start the API server in the background for HTTP-level load tests
# ---------------------------------------------------------------------------
Write-Section "Starting API server for load tests"
$useRedisForServer = if ($redisUp) { "true" } else { "false" }
Write-Host "Server will use USE_REDIS=$useRedisForServer"
$serverJob = Start-Job -ScriptBlock {
    param($backendPath, $pythonExe, $useRedisFlag)
    Set-Location $backendPath
    $env:LLM_PROVIDER = "ollama"
    $env:USE_REDIS = $useRedisFlag
    & $pythonExe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
} -ArgumentList $backend, $pythonExe, $useRedisForServer

$serverReady = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $serverReady = $true; break }
    } catch {}
}

if ($serverReady) {
    Write-Host "Server up at http://127.0.0.1:8000" -ForegroundColor Green

    # ingest a doc so /query has something to retrieve during load test
    try {
        $seedText = [uri]::EscapeDataString("Consistent hashing minimizes key remapping across shards.")
        Invoke-RestMethod -Uri "http://127.0.0.1:8000/ingest/text?doc_id=bench&source=bench&text=$seedText" -Method Post | Out-Null
    } catch {}

    if ($npx) {
        Run-Step "autocannon /health (50 conns, 30s)" {
            npx --yes autocannon -c 50 -d 30 http://127.0.0.1:8000/health
        } "03_autocannon_health.txt"

        Run-Step "autocannon /query (50 conns, 30s)" {
            npx --yes autocannon -c 50 -d 30 -m POST -H "content-type: application/json" `
                -i (Join-Path $backend "loadtest\autocannon-query-body.json") `
                http://127.0.0.1:8000/query
        } "04_autocannon_query.txt"
    }

    if ($locustAvailable) {
        Run-Step "Locust headless (50 users, 60s)" {
            Push-Location $backend
            & $pythonExe -m locust -f loadtest\locustfile.py --host http://127.0.0.1:8000 `
                --headless -u 50 -r 5 -t 60s --csv (Join-Path $resultsDir "05_locust")
            Pop-Location
        } "05_locust.txt"
    } else {
        Write-Host "Skipping Locust step (not installed)." -ForegroundColor Yellow
    }
} else {
    Write-Host "Server did not become ready in 40s -- skipping HTTP load tests." -ForegroundColor Yellow
    Write-Host "Dumping server process output to 06_server_startup_error.txt for debugging..." -ForegroundColor Yellow
    Receive-Job $serverJob -ErrorAction SilentlyContinue | Out-File (Join-Path $resultsDir "06_server_startup_error.txt")
}

Write-Section "Stopping server"
Stop-Job $serverJob -ErrorAction SilentlyContinue | Out-Null
Remove-Job $serverJob -Force -ErrorAction SilentlyContinue | Out-Null

if ($startedRedisContainer) {
    Write-Host "Stopping temporary Redis container (agentmesh-redis-bench)..." -ForegroundColor Cyan
    docker rm -f agentmesh-redis-bench 2>$null | Out-Null
}

# ---------------------------------------------------------------------------
# 5. Combined summary
# ---------------------------------------------------------------------------
Write-Section "Writing summary"
$summaryPath = Join-Path $resultsDir "summary.md"
$summary = @"
# AgentMesh Benchmark Results -- $timestamp

Raw output for each step is in this folder. Quick pointers to what to pull
resume numbers from:

- **01_testbench.txt** -- consistent hashing reshard %, rate limiter
  burst/throughput, circuit breaker trip/recovery time, LRU cache hit ratio
  + ops/sec, priority queue throughput, retrieval Recall/MRR (dense vs BM25
  vs hybrid RRF), contradiction detection precision/recall/F1, end-to-end
  retrieval latency p50/p95.
- **02_openrag_bench.txt** -- multimodal retrieval Recall@k/MRR/nDCG@k,
  broken down by query modality (text / text-table / text-image /
  text-table-image), on the Open RAG Benchmark (or the bundled fixture if
  you haven't downloaded the real dataset yet).
- **03_autocannon_health.txt / 04_autocannon_query.txt** -- HTTP p50/p97.5/
  p99/max latency and req/sec at 50 concurrent connections.
- **05_locust*.csv** -- request stats from a 60s ramped load test (50 users).
  Skipped automatically if Locust/gevent couldn't install on this Python
  version -- autocannon numbers above still cover HTTP-level load.

Check the top of 01_testbench.txt for two lines:
- "Embedding backend: ..." -- must say "(live)" for real semantic retrieval
  numbers; "hash-fallback (offline)" means Ollama wasn't reachable when this
  ran (start 'ollama serve' and re-run).
- "Redis backend: ..." -- must say "redis (live, ...)" for the rate limiter
  and priority queue sections to reflect real Redis WATCH/MULTI/EXEC and
  sorted-set behavior; "in-memory (no Redis)" means those two sections
  measured the local Python fallback instead -- correct code, just not the
  distributed-systems numbers. This script tries to auto-start a Redis
  container via Docker; if that failed, see the Redis warning earlier in
  this run's console output for why.
"@
$summary | Out-File -FilePath $summaryPath -Encoding utf8

Write-Host ""
Write-Host "Done. Results in: $resultsDir" -ForegroundColor Cyan
Write-Host "Start with summary.md, then the individual .txt files." -ForegroundColor Cyan
