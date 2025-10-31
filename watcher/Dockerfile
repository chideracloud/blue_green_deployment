FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1  

WORKDIR /opt/watcher
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY watcher.py .

CMD ["python", "watcher.py"]