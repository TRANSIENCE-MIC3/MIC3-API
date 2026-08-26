FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps .

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "modeling_platform.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
