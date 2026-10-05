set -eu
umask 077
[ "$TRANSFER_ACCESS_KEY" != "$MINIO_ROOT_USER" ] || { echo "Transfer identity must differ from administrator" >&2; exit 1; }
mc alias set storage "$S3_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" --api S3v4 --path on >/dev/null
mc ready storage
mc mb --ignore-existing "storage/$S3_BUCKET"
mc anonymous set none "storage/$S3_BUCKET"
mc admin policy create storage "$POLICY_NAME" /bootstrap/upload-policy.json
mc admin user add storage "$TRANSFER_ACCESS_KEY" "$TRANSFER_SECRET_KEY"
mc admin policy attach storage "$POLICY_NAME" --user "$TRANSFER_ACCESS_KEY"
echo "Artifact bucket and transfer identity configured"
