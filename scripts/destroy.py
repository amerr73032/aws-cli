#!/usr/bin/env python3
"""
destroy.py

Terminate a specified EC2 instance and optionally remove its associated security group.
Requires site configuration for AWS credentials/region and leverages build configuration
for defaults such as the security group name.
"""

import argparse
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from scripts import build as build_script
from utils.shared import resolve_instance_identifier
from utils.configs import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DESTROY_GROUP,
    LOG_LEVEL_CHOICES,
    LOG_LEVEL_DEFAULT,
)
from utils.logger import configure_logger, normalize_log_level, set_logger_level

logger = configure_logger(__name__)


def str_to_bool(value: str) -> bool:
    value = value.lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def parse_args():
    parser = argparse.ArgumentParser(description="Destroy an EC2 instance created by build.py")
    parser.add_argument(
        "--site-config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the site JSON configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--instance",
        required=True,
        help="Instance ID or Name tag value to terminate",
    )
    parser.add_argument(
        "--destroy-group",
        type=str_to_bool,
        default=DEFAULT_DESTROY_GROUP,
        help="Also delete the security group from the build config (default: %(default)s)",
    )
    parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )
    return parser.parse_args()


def load_site_config(path: Path):
    return build_script.load_site_config(path.expanduser().resolve())


def create_session(site_config):
    return boto3.Session(
        aws_access_key_id=site_config["aws_access_key_id"],
        aws_secret_access_key=site_config["aws_secret_access_key"],
        region_name=site_config["region"],
    )


def terminate_instance(ec2_client, instance_id: str):
    logger.info("Terminating instance %s", instance_id)
    try:
        ec2_client.terminate_instances(InstanceIds=[instance_id])
    except ClientError as exc:
        logger.error("Failed to terminate instance %s: %s", instance_id, exc)
        raise


def describe_instance_security_groups(ec2_client, instance_id: str):
    resp = ec2_client.describe_instances(InstanceIds=[instance_id])
    reservations = resp.get("Reservations", [])
    if not reservations:
        logger.warning("No reservation data found for %s; skipping SG capture", instance_id)
        return []
    return reservations[0]["Instances"][0].get("SecurityGroups", [])


def delete_security_groups(ec2_client, groups):
    for group in groups:
        group_id = group["GroupId"]
        group_name = group["GroupName"]
        try:
            logger.info("Deleting security group %s (%s)", group_name, group_id)
            ec2_client.delete_security_group(GroupId=group_id)
        except ClientError as exc:
            logger.error("Failed to delete security group %s (%s): %s", group_name, group_id, exc)
            raise


def run_destroy(site_config_path: Path, instance_id: str, destroy_group: bool):
    site_config = load_site_config(site_config_path)
    session = create_session(site_config)
    ec2_client = session.client("ec2")

    resolved_instance_id = resolve_instance_identifier(ec2_client, instance_id, logger)
    sg_snapshot = describe_instance_security_groups(ec2_client, resolved_instance_id)
    terminate_instance(ec2_client, resolved_instance_id)

    waiter = ec2_client.get_waiter("instance_terminated")
    logger.info("Waiting for instance %s to terminate...", resolved_instance_id)
    waiter.wait(InstanceIds=[resolved_instance_id])
    logger.info("Instance %s terminated.", resolved_instance_id)

    if destroy_group:
        delete_security_groups(ec2_client, sg_snapshot)


def main():
    args = parse_args()
    set_logger_level(logger, args.log)
    try:
        run_destroy(Path(args.site_config), args.instance, args.destroy_group)
    except Exception as exc:
        logger.critical("Destroy failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
