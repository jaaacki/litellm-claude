FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proxy.py config.py cli.py container.py ./
COPY providers/ ./providers/

EXPOSE 2555

CMD ["python", "proxy.py", "2555"]
