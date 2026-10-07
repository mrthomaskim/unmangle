FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY wsgi.py .

RUN useradd --create-home --uid 10001 web
USER web

# Long timeout for cleanup tasks (Cloud Run request timeout is set to 900s).
CMD exec gunicorn --bind :$PORT --workers 2 --threads 8 --timeout 900 --access-logfile - wsgi:app
