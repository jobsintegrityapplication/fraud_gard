"""Create the MLflow artifact bucket on the local S3-compatible store."""
from __future__ import annotations

import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


bucket = os.environ.get('OBJECT_STORE_BUCKET', 'mlflow-artifacts')
client = boto3.client(
    's3',
    endpoint_url=os.environ.get('OBJECT_STORE_ENDPOINT', 'http://minio:9000'),
    aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],
    region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'),
    config=Config(s3={'addressing_style': 'path'}),
)

try:
    client.head_bucket(Bucket=bucket)
except ClientError as exc:
    code = str(exc.response.get('Error', {}).get('Code', ''))
    if code not in {'404', 'NoSuchBucket', 'NotFound'}:
        raise
    client.create_bucket(Bucket=bucket)

print(f'Object-store bucket ready: {bucket}')
