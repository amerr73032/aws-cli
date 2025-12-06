#!/usr/bin/env python3
"""
manage.py

Manage lifecycle actions (start/stop) for existing EC2 instances.
"""

import argparse
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from scripts import build as build_script
from utils.configs import DEFAULT_CONFIG_PATH, LOG_LEVEL_CHOICES, LOG_LEVEL_DEFAULT
from utils.logger import configure_logger, normalize_log_level, set_logger_level
from utils.shared import resolve_instance_identifier

logger = configure_logger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Start or stop an EC2 instance.")
    parser.add_argument(
        "--site-config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the site JSON configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--instance",
        required=True,
        help="Instance ID or Name tag",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="Start the specified instance",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop the specified instance",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display instance details",
    )
    parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )
    return parser.parse_args()


def create_session(site_config):
    return boto3.Session(
        aws_access_key_id=site_config["aws_access_key_id"],
        aws_secret_access_key=site_config["aws_secret_access_key"],
        region_name=site_config["region"],
    )


def describe_instance(ec2_client, instance_id: str):
    logger.debug("Describing instance %s", instance_id)
    resp = ec2_client.describe_instances(InstanceIds=[instance_id])
    reservations = resp.get("Reservations", [])
    if not reservations:
        raise RuntimeError(f"No reservation data found for instance {instance_id}")
    return reservations[0]["Instances"][0]


def start_instance(ec2_client, instance_id: str):
    instance = describe_instance(ec2_client, instance_id)
    state = instance["State"]["Name"]
    if state in {"running", "pending"}:
        logger.info("Instance %s is already %s; nothing to do.", instance_id, state)
        return
    if state not in {"stopped", "stopping"}:
        logger.warning("Instance %s is in '%s'; attempting to start anyway.", instance_id, state)
    logger.info("Starting instance %s...", instance_id)
    logger.debug("Current state: %s", state)
    ec2_client.start_instances(InstanceIds=[instance_id])
    waiter = ec2_client.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])
    logger.info("Instance %s is now running.", instance_id)


def stop_instance(ec2_client, instance_id: str):
    instance = describe_instance(ec2_client, instance_id)
    state = instance["State"]["Name"]
    if state in {"stopped", "stopping"}:
        logger.info("Instance %s is already %s; nothing to do.", instance_id, state)
        return
    if state not in {"running", "pending"}:
        logger.warning("Instance %s is in '%s'; attempting to stop anyway.", instance_id, state)
    logger.info("Stopping instance %s...", instance_id)
    logger.debug("Current state: %s", state)
    ec2_client.stop_instances(InstanceIds=[instance_id])
    waiter = ec2_client.get_waiter("instance_stopped")
    waiter.wait(InstanceIds=[instance_id])
    logger.info("Instance %s is now stopped.", instance_id)


def show_instance(ec2_client, instance):
    instance_id = instance["InstanceId"]
    instance_type = instance["InstanceType"]
    state = instance["State"]["Name"]
    name = next((tag["Value"] for tag in instance.get("Tags", []) if tag["Key"] == "Name"), "")
    zone = instance.get("Placement", {}).get("AvailabilityZone")
    public_ip = instance.get("PublicIpAddress")
    private_ip = instance.get("PrivateIpAddress")
    key_name = instance.get("KeyName")
    security_groups = ", ".join(sg["GroupName"] for sg in instance.get("SecurityGroups", []))

    logger.info("Instance details for %s", instance_id)
    logger.info("  Name: %s", name or "<none>")
    logger.info("  Type: %s", instance_type)
    logger.info("  State: %s", state)
    logger.info("  AZ: %s", zone)
    logger.info("  Public IP: %s", public_ip or "n/a")
    logger.info("  Private IP: %s", private_ip or "n/a")
    logger.info("  Key Name: %s", key_name or "n/a")
    logger.info("  Security Groups: %s", security_groups or "n/a")

    try:
        itype = ec2_client.describe_instance_types(InstanceTypes=[instance_type])["InstanceTypes"][0]
        vcpus = itype["VCpuInfo"].get("DefaultVCpus")
        memory_mib = itype["MemoryInfo"].get("SizeInMiB")
        logger.info("  vCPUs: %s", vcpus)
        logger.info("  RAM: %.2f GB", (memory_mib or 0) / 1024 if memory_mib else 0)
    except ClientError as exc:
        logger.debug("Unable to describe instance type %s: %s", instance_type, exc)

    # storage info (first EBS volume)
    block = next((bdm for bdm in instance.get("BlockDeviceMappings", []) if bdm.get("Ebs")), None)
    if block:
        volume_id = block["Ebs"]["VolumeId"]
        try:
            volume = ec2_client.describe_volumes(VolumeIds=[volume_id])["Volumes"][0]
            size = volume.get("Size")
            logger.info("  Root volume %s size: %s GB", volume_id, size)
        except ClientError as exc:
            logger.debug("Unable to describe volume %s: %s", volume_id, exc)


def run_manage(site_config_path: Path, instance_identifier: str, start: bool, stop: bool, show: bool):
    if not any([start, stop, show]):
        raise ValueError("You must specify --start, --stop, and/or --show.")
    site_config = build_script.load_site_config(site_config_path.expanduser().resolve())
    session = create_session(site_config)
    ec2_client = session.client("ec2")
    instance_id = resolve_instance_identifier(ec2_client, instance_identifier, logger)
    instance = describe_instance(ec2_client, instance_id)

    if show:
        show_instance(ec2_client, instance)
    if start:
        start_instance(ec2_client, instance_id)
    if stop:
        stop_instance(ec2_client, instance_id)


def main():
    args = parse_args()
    set_logger_level(logger, args.log)
    try:
        run_manage(Path(args.site_config), args.instance, args.start, args.stop, args.show)
    except (ClientError, ValueError, RuntimeError) as exc:
        logger.critical("Manage command failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
