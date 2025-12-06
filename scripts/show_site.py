#!/usr/bin/env python3
"""
show_site.py

Quick connectivity test against the configured AWS site. Uses the shared site
JSON configuration (configs/site_config.json), validates the AWS session, and
prints basic information including the most relevant public IP and running instances.
"""

import argparse
from pathlib import Path
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from scripts import build as build_script
from utils.configs import DEFAULT_CONFIG_PATH, LOG_LEVEL_CHOICES, LOG_LEVEL_DEFAULT
from utils.logger import configure_logger, normalize_log_level, set_logger_level

logger = configure_logger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Show connection details for the configured AWS site."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the JSON configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )
    return parser.parse_args()


def describe_instances(ec2_client) -> List[Dict]:
    """
    Return a list of instance summaries for the configured region.
    """
    summaries: List[Dict] = []
    paginator = ec2_client.get_paginator("describe_instances")
    for page in paginator.paginate():
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
                summaries.append(
                    {
                        "instance_id": instance["InstanceId"],
                        "state": instance.get("State", {}).get("Name", "unknown"),
                        "type": instance.get("InstanceType"),
                        "public_ip": instance.get("PublicIpAddress"),
                        "public_dns": instance.get("PublicDnsName"),
                        "name": tags.get("Name"),
                    }
                )
    return summaries


def resolve_site_public_ip(instances: List[Dict]) -> Optional[str]:
    """
    Determine the most relevant public IP to display.
    Preference order: running instance with a public IP.
    """
    for instance in instances:
        if instance.get("public_ip"):
            return instance["public_ip"]
    return None


def run_show_site(config_path: Path) -> Dict:
    """
    Entry point consumable from other modules. Returns collected data.
    """
    config_path = config_path.expanduser().resolve()
    config = build_script.load_site_config(config_path)

    profile = config["profile_name"]
    region = config["region"]
    access_key = config["aws_access_key_id"]
    secret_key = config["aws_secret_access_key"]

    logger.info("Testing AWS connection using profile=%s region=%s", profile, region)
    session = boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    sts_client = session.client("sts")
    ec2_client = session.client("ec2")

    identity = sts_client.get_caller_identity()
    logger.info("Authenticated as %s", identity.get("Arn"))

    try:
        vpc_resp = ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
        default_vpc_id = vpc_resp.get("Vpcs", [{}])[0].get("VpcId")
    except ClientError as exc:
        logger.error("Unable to determine default VPC: %s", exc)
        default_vpc_id = None

    instances = describe_instances(ec2_client)
    logger.info("Found %d EC2 instances in region %s", len(instances), region)

    site_public_ip = resolve_site_public_ip(instances)
    if site_public_ip:
        logger.info("Site public IP: %s", site_public_ip)
    else:
        logger.info("No public IP found for this site.")

    return {
        "profile": profile,
        "region": region,
        "config_path": str(config_path),
        "identity": identity,
        "default_vpc_id": default_vpc_id,
        "site_public_ip": site_public_ip,
        "instances": instances,
    }


def format_result(data: Dict) -> str:
    """
    Produce a human-readable summary of the collected data.
    """
    lines = [
        f"Profile:      {data['profile']}",
        f"Region:       {data['region']}",
        f"Caller ARN:   {data['identity'].get('Arn')}",
        f"Account ID:   {data['identity'].get('Account')}",
        f"Default VPC:  {data.get('default_vpc_id') or 'unknown'}",
        f"Config Path:  {data['config_path']}",
        f"Site Pub IP:  {data.get('site_public_ip') or 'not available'}",
    ]

    lines.append("Instances:")
    if not data["instances"]:
        lines.append("  - none found")
    else:
        for inst in data["instances"]:
            lines.append(
                "  - {id} [{state}] {itype} {ip} {name}".format(
                    id=inst["instance_id"],
                    state=inst["state"],
                    itype=inst["type"],
                    ip=inst["public_ip"] or "no public ip",
                    name=f"({inst['name']})" if inst.get("name") else "",
                ).rstrip()
            )
    return "\n".join(lines)


def main():
    args = parse_args()
    set_logger_level(logger, args.log)
    data = run_show_site(Path(args.config))
    print(format_result(data))


if __name__ == "__main__":
    main()
