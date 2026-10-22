# Local Kafka

From the repository root, with `.env` configured:

```powershell
docker compose up -d --wait postgres kafka
docker compose run --rm kafka-init
python -m pip install -e ".[messaging]"
python -m alembic upgrade head
python -m mic3_api.publisher
# In a second terminal:
python -m mic3_api.worker
```

Host clients use `127.0.0.1:9092`; Docker clients use `kafka:19092`.
Stop either process with Ctrl+C. The worker accepts runs but launches no Jobs.
EOSC deployment is not implemented yet.
