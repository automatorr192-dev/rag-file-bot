FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --create-home app && mkdir -p /app/data && chown app /app/data
USER app
RUN python -c "from models import encode; encode(['warmup'])"
CMD ["python", "bot.py"]
