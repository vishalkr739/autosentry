import boto3
from botocore.exceptions import ClientError

from .base import SecretsProvider


class AwsSecretsManagerProvider(SecretsProvider):
    def __init__(self, region_name: str | None = None):
        self._client = boto3.client("secretsmanager", region_name=region_name)

    def get_secret(self, name: str) -> str:
        try:
            response = self._client.get_secret_value(SecretId=name)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                raise KeyError(name) from e
            raise
        if "SecretString" not in response:
            raise KeyError(f"secret {name!r} has no SecretString (binary secrets are not supported)")
        return response["SecretString"]

    def get_secret_version(self, name: str) -> str | None:
        response = self._client.describe_secret(SecretId=name)
        return next(
            (
                vid
                for vid, stages in response["VersionIdsToStages"].items()
                if "AWSCURRENT" in stages
            ),
            None,
        )
