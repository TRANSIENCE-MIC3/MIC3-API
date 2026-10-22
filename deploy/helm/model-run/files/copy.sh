set -eu
umask 077
# An empty model output is a verification failure, not a transfer failure.
# Keep traversal errors fatal; do not hide them inside the condition below.
output_files=$(mc ls --recursive --json /outputs/)
if [ -z "$output_files" ]; then
  echo "No output files to upload; worker will verify the output contract."
  exit 0
fi
mc alias set storage "$S3_ENDPOINT" "$AWS_ACCESS_KEY_ID" "$AWS_SECRET_ACCESS_KEY" --api S3v4 --path on >/dev/null
exec mc cp --recursive --json --max-workers 2 /outputs/ "storage/$S3_BUCKET/$MODEL_ID/sha256-$MODEL_IMAGE_SHA/$RUN_ID/outputs/"
