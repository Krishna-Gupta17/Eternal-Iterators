# Deployment Guide: Production Deployment with PostgreSQL

This guide covers deploying the CIT Timetable & Venue Allocation Platform to **Render** (or any container host like Railway / Fly.io) using a free, persistent **PostgreSQL** database (from **Neon.tech**, **Supabase**, or **Render PostgreSQL**).

---

## Why PostgreSQL for Production?
By default, the platform uses a local SQLite database with WAL mode for zero-setup local development. In production container platforms like Render:
- Free-tier container disks are **ephemeral** and reset on every restart or idle spin-down.
- Mounting persistent disks on Render requires a paid plan ($7/mo disk add-on).
- Supplying a free `DATABASE_URL` stores the application state and generated schedule exports directly in persistent PostgreSQL tables (`app_state` and `schedule_exports`). On container boot, the application rehydrates exports to `/app/exports/` automatically.

---

## 1. Create a Free Managed PostgreSQL Database

### Option A: Neon.tech (Recommended — Serverless & Instant)
1. Sign up at [neon.tech](https://neon.tech) (Free tier includes 0.5 GB storage).
2. Create a new project (e.g. `cit-scheduler-db`).
3. Under the **Dashboard**, copy the **Connection string**:
   ```
   postgresql://user:password@ep-xyz.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```

### Option B: Supabase
1. Sign up at [supabase.com](https://supabase.com).
2. Create a new project.
3. Under **Project Settings > Database**, copy the **URI**:
   ```
   postgresql://postgres:[YOUR-PASSWORD]@db.xxxx.supabase.co:5432/postgres
   ```

### Option C: Render PostgreSQL
1. In your Render dashboard, click **New + > PostgreSQL**.
2. Give it a name (e.g., `cit-postgres`) and choose the Free tier.
3. Once provisioned, copy the **Internal Database URL** (if deploying on Render) or **External Database URL**.

---

## 2. Deploy Web Service to Render

1. Push your repository to GitHub.
2. In the Render Dashboard, click **New + > Web Service**.
3. Select your GitHub repository (`Eternal-Iterators`).
4. Configure the service:
   - **Environment:** `Docker` (Render will automatically detect the root `Dockerfile`)
   - **Region:** Choose the region closest to your PostgreSQL database (e.g., Oregon or Frankfurt)
   - **Instance Type:** `Free` or `Starter`
5. Under **Environment Variables**, add:

   | Key | Value | Notes |
   | --- | --- | --- |
   | `DATABASE_URL` | `postgresql://user:pass@host/dbname` | Paste your PostgreSQL URI here. (Legacy `postgres://` prefix is auto-rewritten to `postgresql+psycopg2://`) |
   | `JWT_SECRET` | *(Random 32+ char string)* | Used to sign auth session tokens |
   | `DEMO_PASSWORD` | `demo123` | Shared password for demo accounts |
   | `TEAM_NAME` | `Eternal Iterators` | Emitted into exported schedule metadata |
   | `LLM_PROVIDER` | `none` *(or gemini/anthropic/openai)* | Optional: leave empty or `none` for built-in rule-based NLP |
   | `LLM_API_KEY` | *(Optional API Key)* | Only needed if `LLM_PROVIDER` is set |

6. Click **Deploy Web Service**.

---

## 3. Verify Deployment

1. Once the build completes and the container starts, open your deployed URL (e.g., `https://cit-scheduler.onrender.com`).
2. Log in with an admin account (`admin1` / `demo123`).
3. Click **Generate Timetable** or perform a booking.
4. Trigger a container restart from the Render dashboard.
5. Reload the app: notice that the active timetable, Spectra booking placements, and Admin approvals remain intact, restored directly from PostgreSQL.
