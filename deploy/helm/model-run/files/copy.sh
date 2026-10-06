set -eu
umask 077
mc alias set storage "$S3_ENDPOINT" "$AWS_ACCESS_KEY_ID" "$AWS_SECRET_ACCESS_KEY" --api S3v4 --path on >/dev/null
exec mc cp --recursive --json --max-workers 2 /outputs/ "storage/$S3_BUCKET/$MODEL_ID/sha256-$MODEL_IMAGE_SHA/$RUN_ID/outputs/"
