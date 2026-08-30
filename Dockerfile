FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apk upgrade --no-cache

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY task1.py analysis.py database.py api.py ./

CMD ["python", "task1.py"]