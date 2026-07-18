import os
import sys
import time
import subprocess
import json
import csv

def run_cmd(cmd, shell=True):
    print(f"\n> Running: {cmd}")
    res = subprocess.run(cmd, shell=shell)
    if res.returncode != 0:
        print(f"[Warn/Error] Command failed with code {res.returncode}")
    return res

def main():
    print("=============================================================")
    # 1. Start Docker services
    # 1. Start and scale Docker services
    print("[1/3] Starting and scaling Docker containers (3 backend instances)...")
    run_cmd("docker-compose down")
    run_cmd("docker-compose up -d --build --scale backend=3")
    time.sleep(5) # Wait for containers to initialize

    # 3. Run Headless Locust Load Test (15 seconds, 10 users)
    # 2. Run Headless Locust Load Test (15 seconds, 10 users)
    print("[2/3] Executing headless Locust load test (15s duration, 10 concurrent users)...")
    backend_dir = os.path.join(os.getcwd(), "backend")
    results_prefix = os.path.join(backend_dir, "loadtest", "results")
    
    # Clean up previous csv results if any
    for suffix in ["_stats.csv", "_stats_history.csv", "_failures.csv", "_exceptions.csv"]:
        f_path = results_prefix + suffix
        if os.path.exists(f_path):
            os.remove(f_path)

    # Find dynamic host port for backend-1
    import re
    res_port = subprocess.run(["docker", "port", "agentmesh-backend-1", "8000"], capture_output=True, text=True)
    ports = re.findall(r":(\d+)", res_port.stdout)
    host_port = int(ports[0]) if ports else 8000
    print(f"Detected backend-1 running on host port: {host_port}")

    run_cmd(
        f"locust -f backend/loadtest/locustfile.py --host http://localhost:{host_port} "
        f"--headless -u 10 -r 2 -t 15s --csv={results_prefix}"
    )

    # 3. Compile and Print Unified System Report
    print("=============================================================")
    print("                   SYSTEM LOAD TEST REPORT                   ")
    print("=============================================================")
    
    print("Load Test Performance (FastAPI + Scaled Workers + Redis):")
    stats_csv = results_prefix + "_stats.csv"
    if os.path.exists(stats_csv):
        try:
            with open(stats_csv, newline='') as f:
                reader = csv.reader(f)
                rows = list(reader)
                # Find total aggregated row
                for r in rows:
                    if r and len(r) > 1 and r[1].strip().lower() == "aggregated":
                        print(f"  - Total Requests : {r[2]}")
                        print(f"  - Failed Requests: {r[3]} ({float(r[3])/float(r[2])*100:.1f}%)" if float(r[2]) > 0 else f"  - Failed Requests: {r[3]}")
                        print(f"  - Avg Response   : {float(r[5]):.2f} ms")
                        print(f"  - Max Response   : {float(r[7]):.2f} ms")
                        print(f"  - P95 Response   : {float(r[16]):.2f} ms")
                        print(f"  - Throughput     : {float(r[9]):.2f} RPS")
                        break
        except Exception as e:
            print(f"Failed to parse load test CSV: {e}")
    else:
        print("[Error] Locust stats CSV not found. Make sure Docker is running on your machine.")

    print("=============================================================")

if __name__ == "__main__":
    main()
