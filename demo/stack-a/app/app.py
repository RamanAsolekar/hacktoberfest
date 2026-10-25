"""Orders service (demo). Connects to Postgres and keeps a heartbeat query running."""
import os
import sys
import time

import psycopg

dsn = dict(host=os.environ.get("DB_HOST", "db"), dbname=os.environ.get("DB_NAME", "orders"),
           user=os.environ.get("DB_USER", "postgres"), password=os.environ.get("POSTGRES_PASSWORD"),
           connect_timeout=5)
print("orders-service starting, connecting to", dsn["host"], flush=True)
try:
    conn = psycopg.connect(**dsn)
except Exception as exc:
    print(f"FATAL: database connection failed: {exc}", flush=True)
    sys.exit(1)
print("connected to postgres; orders-service ready", flush=True)
while True:
    conn.execute("SELECT 1")
    time.sleep(5)
