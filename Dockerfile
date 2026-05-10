FROM docker.1ms.run/library/python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

CMD ["python", "run_server.py", "--v7-api", "--host", "0.0.0.0", "--port", "8787"]
