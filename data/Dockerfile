# data/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Copy shared code AND service-specific code
COPY requirements.txt .
COPY shared/ ./shared/
COPY data/ ./data/

RUN pip install --no-cache-dir -r requirements.txt

# Command to run the Flask app
CMD ["python", "data/main.py"]