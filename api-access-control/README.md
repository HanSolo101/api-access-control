# Quotes API Access-Control Service

A high-performance access-control and rate-limiting service built with **FastAPI**, **Redis**, and **PostgreSQL** behind an **Nginx** round-robin load balancer.

Designed and implemented in full accordance with `DESIGN.md`.

---

## 🏗️ Architecture

```
                          ┌─────────────────────────┐
                          │   Nginx (Port 8000)     │
                          │   Round-Robin Balancer  │
                          └───────────┬─────────────┘
                                      │
            ┌─────────────────────────┼─────────────────────────┐
            ▼                         ▼                         ▼
   ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
   │ FastAPI (App 1) │       │ FastAPI (App 2) │       │ FastAPI (App 3) │
   └────────┬────────┘       └────────┬────────┘       └────────┬────────┘
            │                         │                         │
            └─────────────────────────┼─────────────────────────┘
                                      │
               ┌──────────────────────┴──────────────────────┐
               ▼                                             ▼
     ┌───────────────────┐                         ┌───────────────────┐
     │  Redis (Port 6379)│                         │Postgres(Port 5432)│
     │  - Lua TokenBucket│                         │- Source of Truth  │
     │  - Monthly Quota  │                         │- Usage Ledger     │
     │  - Key Cache      │                         │- Orgs & Plans     │
     └───────────────────┘                         └───────────────────┘
```

- **PostgreSQL** is the source of truth (Organizations, Plans, API Keys, Usage Ledger).
- **Redis** is a fast, rebuildable helper (Atomic Lua script executes token-bucket burst checks and UTC monthly quota reservations).
- **3 Stateless FastAPI instances** behind Nginx allow any instance to serve any request.

---

## 🚀 Quick Start

### 1. Launch the Cluster
Make sure Docker Desktop is running, then run:
```powershell
docker compose up --build -d
```

### 2. Seed Initial Database & Plans
```powershell
docker compose exec app1 python -m src.app.seed
```
This generates default plans (`free`, `pro`, `scale`), a demo organization (`Acme Corp`), and outputs the primary demo API key (format: `qk_<8-char-id>_<43-char-secret>`).

### 3. Open Interactive Swagger Docs
Navigate to:
👉 **http://localhost:8000/docs**

---

## 📡 Key Endpoints

### 1. Quotes Endpoint (`/v1/quote`)
Protected by `Authorization: Bearer <key>`.
Supports both `GET` and `POST`.

```bash
curl http://localhost:8000/v1/quote \
  -H "Authorization: Bearer qk_..."
```

**Headers Returned:**
- `X-RateLimit-Limit`: Maximum burst allowed.
- `X-RateLimit-Remaining`: Tokens left in the current bucket.
- `X-RateLimit-Reset`: Unix timestamp when quota resets (start of next UTC month).

**Idempotency (`Idempotency-Key` header):**
```bash
curl http://localhost:8000/v1/quote \
  -H "Authorization: Bearer qk_..." \
  -H "Idempotency-Key: my-unique-req-001"
```
Replayed requests with the same key return the cached quote without deducting quota or double-billing.

### 2. Usage Reporting (`GET /usage`)
View consumption breakdown over time (by `hour` or `day`) without deducting quota:
```bash
curl "http://localhost:8000/usage?granularity=day" \
  -H "Authorization: Bearer qk_..."
```

### 3. Admin API (`/admin/*`)
Protected by header `X-Admin-Token: super-secret-admin-token`:
- `POST /admin/orgs` - Create organization.
- `POST /admin/keys` - Create new key for an org (returned once).
- `POST /admin/keys/{id}/rotate` - Rotate key with grace period (`expires_at`).
- `POST /admin/keys/{id}/revoke` - Immediately revoke key and invalidate Redis cache.
- `PATCH /admin/orgs/{id}/plan` - Update org plan with immediate cache invalidation.

---

## 🧪 Benchmarking & Reconciler

### Run 200 RPS Load Test
```powershell
python benchmark.py <YOUR_API_KEY>
```
Fires concurrent requests against the cluster, measuring p50, p90, p95, and p99 latency percentiles and verifying rate-limiter accuracy.

### Run Database Reconciler
```powershell
docker compose exec app1 python -m src.app.reconcile
```
Scans Postgres ledger and reconciles any Redis counter drift.
