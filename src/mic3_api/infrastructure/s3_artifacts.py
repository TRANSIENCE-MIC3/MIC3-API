"""Read-only artifact inventory; ETags are intentionally not interpreted."""
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from mic3_api.application.runs.execution import Artifact, InfrastructureUnavailable


class S3ArtifactInventory:
    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    @classmethod
    def connect(cls, settings):
        client = boto3.client('s3', endpoint_url=settings.artifact_endpoint,
            aws_access_key_id=settings.artifact_access_key.get_secret_value(),
            aws_secret_access_key=settings.artifact_secret_key.get_secret_value(),
            region_name='us-east-1', config=Config(connect_timeout=3, read_timeout=10,
                retries={'total_max_attempts': 2, 'mode': 'standard'}, s3={'addressing_style': 'path'}))
        return cls(client, settings.artifact_bucket)

    def list(self, prefix):
        try:
            pages = self.client.get_paginator('list_objects_v2').paginate(Bucket=self.bucket, Prefix=prefix)
            return tuple(Artifact(self.bucket, obj['Key'], obj['Size'])
                         for page in pages for obj in page.get('Contents', []))
        except (BotoCoreError, ClientError) as exc:
            raise InfrastructureUnavailable('Artifact inventory unavailable.') from exc

    def close(self):
        self.client.close()
