"""Shared helpers for script utilities (instance lookup, etc.)."""

import re

from botocore.exceptions import ClientError

INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")


def resolve_instance_identifier(ec2_client, identifier: str, logger) -> str:
    """
    Resolve a user-supplied identifier (instance ID or Name tag) to an instance ID.
    """
    if INSTANCE_ID_PATTERN.match(identifier):
        return identifier

    logger.info("Looking up instance by Name tag: %s", identifier)
    try:
        resp = ec2_client.describe_instances(
            Filters=[
                {"Name": "tag:Name", "Values": [identifier]},
                {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
            ],
        )
    except ClientError as exc:
        logger.error("Failed to search for instance %s: %s", identifier, exc)
        raise

    for reservation in resp.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            instance_id = instance["InstanceId"]
            logger.info("Resolved name '%s' to instance %s", identifier, instance_id)
            return instance_id

    raise ValueError(f"No instance found with Name tag '{identifier}'")
