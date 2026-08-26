FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY src ./src

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "modeling_platform.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
