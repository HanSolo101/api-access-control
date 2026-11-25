"""Benchmark and Load Test Script (Section 5 in DESIGN.md):
- Fires requests concurrently at a target rate (e.g., 200 RPS) through Nginx.
- Measures p50, p90, p95, p99 latencies.
- Tracks HTTP status codes (200 OK vs 429 Rate Limited).
- Pure standard library Python: no extra packages needed!
"""
import asyncio
import time
import json
import statistics
import sys
from urllib.parse import urlparse


async def send_request(host: str, port: int, path: str, api_key: str):
    start = time.perf_counter()
    reader, writer = await asyncio.open_connection(host, port)
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Authorization: Bearer {api_key}\r\n"
        f"User-Agent: BenchmarkClient/1.0\r\n"
        f"Connection: close\r\n\r\n"
    )
    writer.write(req.encode())
    await writer.drain()

    response_line = await reader.readline()
    status_code = int(response_line.split()[1]) if response_line else 500

    # Drain rest of headers and body
    await reader.read()
    writer.close()
    await writer.wait_closed()
    latency_ms = (time.perf_counter() - start) * 1000
    return status_code, latency_ms


async def run_benchmark(
    base_url: str = "http://localhost:8000",
    api_key: str = "qk_195fb2a7_fOPOV4CFs-hO15YUUntT98dr4KA6IpT7VKYIaCGDKGM",
    target_rps: int = 200,
    duration_seconds: int = 5,
):
    parsed = urlparse(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8000
    path = "/v1/quote"

    total_requests = target_rps * duration_seconds
    interval = 1.0 / target_rps

    print(f"=== Starting Benchmark ===")
    print(f"Target URL:        {base_url}{path}")
    print(f"Target RPS:        {target_rps} req/sec")
    print(f"Duration:          {duration_seconds} seconds")
    print(f"Total Requests:    {total_requests}")
    print("=" * 30)

    latencies = []
    status_counts = {}

    start_time = time.perf_counter()
    tasks = []

    for i in range(total_requests):
        task = asyncio.create_task(send_request(host, port, path, api_key))
        tasks.append(task)
        await asyncio.sleep(interval)

    results = await asyncio.gather(*tasks, return_exceptions=True)
    total_duration = time.perf_counter() - start_time

    for res in results:
        if isinstance(res, tuple):
            status_code, latency = res
            status_counts[status_code] = status_counts.get(status_code, 0) + 1
            latencies.append(latency)
        else:
            status_counts["ERROR"] = status_counts.get("ERROR", 0) + 1

    latencies.sort()
    actual_rps = len(latencies) / total_duration if total_duration > 0 else 0

    print("\n=== Benchmark Results ===")
    print(f"Total Completed:   {len(latencies)} in {total_duration:.2f}s")
    print(f"Effective RPS:     {actual_rps:.1f} req/sec")
    print(f"Status Codes:      {status_counts}")

    if latencies:
        p50 = statistics.median(latencies)
        p90 = latencies[int(len(latencies) * 0.90)]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        print(f"Latency p50:       {p50:.2f} ms")
        print(f"Latency p90:       {p90:.2f} ms")
        print(f"Latency p95:       {p95:.2f} ms")
        print(f"Latency p99:       {p99:.2f} ms")
        print(f"Min / Max:         {latencies[0]:.2f} ms / {latencies[-1]:.2f} ms")
    print("=" * 30)


if __name__ == "__main__":
    key = sys.argv[1] if len(sys.argv) > 1 else "qk_195fb2a7_fOPOV4CFs-hO15YUUntT98dr4KA6IpT7VKYIaCGDKGM"
    asyncio.run(run_benchmark(api_key=key, target_rps=200, duration_seconds=5))
