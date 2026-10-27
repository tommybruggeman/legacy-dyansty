# Built from the repo root so the API can import the site's Python packages:
#   docker build -f api/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./requirements.txt
COPY api/requirements.txt ./api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt
COPY . .
# Cloud Run sets PORT; it scales to zero when nobody is using the app.
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080}
