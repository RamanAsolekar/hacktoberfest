FROM python:3.11-slim
RUN pip install --no-cache-dir "psycopg[binary]==3.2.3"
COPY app.py /app.py
CMD ["python", "-u", "/app.py"]
