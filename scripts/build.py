#!/usr/bin/env python3
"""
build.py

Provision an EC2 instance in AWS using shared site configuration
(configs/site_config.json) and build-specific parameters (configs/build_config.json).

- Resolves an AMI either directly or via an SSM Parameter (specified in build config)
- Creates or reuses a security group in the default VPC
- Logs actions with Python's logging module
- Site configuration defaults live in configs/site_config.json (override via --site-config)
- Build configuration defaults live in configs/build_config.json (override via --build-config)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Optional

import boto3
from botocore.exceptions import ClientError

from utils.configs import (
    DEFAULT_BUILD_CONFIG_PATH,
    DEFAULT_CONFIG_PATH,
    LOG_LEVEL_CHOICES,
    LOG_LEVEL_DEFAULT,
)
from utils.logger import configure_logger, normalize_log_level, set_logger_level

# ==================== CONFIGURATION ====================

PROFILE_NAME = ""
REGION = ""
INSTANCE_TYPE = ""
KEY_NAME = ""
SECURITY_GROUP_NAME = ""
INSTANCE_NAME = ""
AMI_ID = ""
AMI_SSM_PARAMETER = ""
AWS_ACCESS_KEY_ID = ""
AWS_SECRET_ACCESS_KEY = ""

logger = configure_logger(__name__)


def load_site_config(path: Path) -> Dict:
    """
    Load site configuration data from the given JSON file.
    """
    logger.info("Loading site configuration from %s", path)
    try:
        with open(path, "r") as config_file:
            data = json.load(config_file)
    except OSError as e:
        logger.critical("Failed to read site config file %s: %s", path, e)
        raise
    except json.JSONDecodeError as e:
        logger.critical("Site config file %s is not valid JSON: %s", path, e)
        raise
    return data


def load_build_config(path: Path) -> Dict:
    """
    Load build configuration values for EC2 provisioning.
    """
    logger.info("Loading build configuration from %s", path)
    try:
        with open(path, "r") as config_file:
            data = json.load(config_file)
    except OSError as e:
        logger.critical("Failed to read build config file %s: %s", path, e)
        raise
    except json.JSONDecodeError as e:
        logger.critical("Build config file %s is not valid JSON: %s", path, e)
        raise
    return data


def apply_config(site_config: Dict, build_config: Dict):
    """
    Populate module-level configuration values from loaded JSON sources.
    Site config is responsible for AWS connectivity, build config for EC2 build details.
    """
    site_keys = ["profile_name", "region", "aws_access_key_id", "aws_secret_access_key"]
    build_keys = [
        "instance_type",
        "key_name",
        "security_group_name",
        "instance_name",
        "instance_profile",
    ]

    missing_site = [key for key in site_keys if key not in site_config]
    missing_build = [key for key in build_keys if key not in build_config]
    if missing_site:
        raise KeyError(f"Missing site configuration keys: {', '.join(missing_site)}")
    if missing_build:
        raise KeyError(f"Missing build configuration keys: {', '.join(missing_build)}")
    if not (build_config.get("ami_id") or build_config.get("ami_ssm_parameter")):
        raise KeyError("Build configuration must include 'ami_id' or 'ami_ssm_parameter'")

    global PROFILE_NAME, REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    PROFILE_NAME = site_config["profile_name"]
    REGION = site_config["region"]
    AWS_ACCESS_KEY_ID = site_config["aws_access_key_id"]
    AWS_SECRET_ACCESS_KEY = site_config["aws_secret_access_key"]

    global INSTANCE_TYPE, KEY_NAME, SECURITY_GROUP_NAME, INSTANCE_NAME, AMI_ID, AMI_SSM_PARAMETER, INSTANCE_PROFILE
    INSTANCE_TYPE = build_config["instance_type"]
    KEY_NAME = build_config["key_name"]
    SECURITY_GROUP_NAME = build_config["security_group_name"]
    INSTANCE_NAME = build_config["instance_name"]
    INSTANCE_PROFILE = build_config["instance_profile"]
    AMI_ID = build_config.get("ami_id", "")
    AMI_SSM_PARAMETER = build_config.get("ami_ssm_parameter", "")


# ====================== HELPER FUNCTIONS ======================

def get_session() -> boto3.Session:
    """
    Create a boto3 Session using explicit credentials from the config.
    """
    logger.debug("Creating boto3 session with region=%s using inline credentials", REGION)
    try:
        session = boto3.Session(
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=REGION,
        )
    except Exception as exc:
        logger.error("Failed to create boto3 Session: %s", exc)
        raise
    return session


def load_bootstrap(path: Optional[Path]) -> Optional[str]:
    """Return bootstrap/user-data content when provided."""
    if not path:
        return None
    bootstrap_path = path.expanduser().resolve()
    logger.info("Loading bootstrap script from %s", bootstrap_path)
    try:
        return bootstrap_path.read_text()
    except OSError as exc:
        logger.critical("Failed to read bootstrap script %s: %s", bootstrap_path, exc)
        raise


def get_default_vpc_id(ec2_client) -> str:
    """
    Return the default VPC ID for the current region.
    """
    logger.info("Looking up default VPC...")
    try:
        resp = ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    except ClientError as e:
        logger.error("Error describing VPCs: %s", e)
        raise

    vpcs = resp.get("Vpcs", [])
    if not vpcs:
        logger.critical("No default VPC found in region %s", REGION)
        raise RuntimeError("No default VPC found")

    vpc_id = vpcs[0]["VpcId"]
    logger.info("Default VPC: %s", vpc_id)
    return vpc_id


def get_or_create_security_group(ec2_client, vpc_id: str) -> str:
    """
    Find an existing security group by name in the given VPC or create it.
    """
    logger.info("Ensuring security group %s exists in VPC %s", SECURITY_GROUP_NAME, vpc_id)

    # Try to find existing SG
    try:
        resp = ec2_client.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [SECURITY_GROUP_NAME]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        )
        sgs = resp.get("SecurityGroups", [])
        if sgs:
            sg = sgs[0]
            sg_id = sg["GroupId"]
            logger.info("Reusing existing security group %s (%s)", SECURITY_GROUP_NAME, sg_id)
            return sg_id
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code != "InvalidGroup.NotFound":
            logger.error("Error describing security groups: %s", e)
            raise
        logger.debug("Security group not found; will create a new one")

    # Create new SG
    logger.info("Creating security group %s", SECURITY_GROUP_NAME)
    try:
        resp = ec2_client.create_security_group(
            GroupName=SECURITY_GROUP_NAME,
            Description=f"SG for {INSTANCE_NAME} instance",
            VpcId=vpc_id,
        )
        sg_id = resp["GroupId"]
        logger.info("Created security group %s (%s)", SECURITY_GROUP_NAME, sg_id)
    except ClientError as e:
        logger.error("Error creating security group: %s", e)
        raise

    return sg_id


def resolve_ami_id(ssm_client) -> str:
    """
    Resolve the AMI ID based on build configuration.
    Preference order: explicit AMI ID, then lookup via SSM parameter.
    """
    if AMI_ID:
        logger.info("Using AMI ID specified in build configuration: %s", AMI_ID)
        return AMI_ID

    logger.info("Resolving AMI ID from SSM parameter: %s", AMI_SSM_PARAMETER)
    try:
        resp = ssm_client.get_parameter(Name=AMI_SSM_PARAMETER)
        ami_id = resp["Parameter"]["Value"]
        logger.info("Resolved AMI ID: %s", ami_id)
        return ami_id
    except ClientError as e:
        logger.critical("Failed to resolve AMI from SSM: %s", e)
        raise


def parse_args():
    """
    Parse command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Create an EC2 instance using site + build configs."
    )
    parser.add_argument(
        "--site-config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the site JSON configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--build-config",
        default=str(DEFAULT_BUILD_CONFIG_PATH),
        help="Path to the build JSON configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--bootstrap",
        help="Path to a user-data/bootstrap script to run on first boot",
    )
    parser.add_argument(
        "--volume-size",
        type=int,
        help="Override root volume size (GB)",
    )
    parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )
    return parser.parse_args()


# =========================== MAIN ============================

def build_block_device_mappings(ec2_client, ami_id: str, volume_size: int):
    resp = ec2_client.describe_images(ImageIds=[ami_id])
    images = resp.get("Images", [])
    if not images:
        raise RuntimeError(f"Unable to describe AMI {ami_id} to override volume size")
    image = images[0]
    root_device = image["RootDeviceName"]
    for mapping in image.get("BlockDeviceMappings", []):
        if mapping.get("DeviceName") == root_device and mapping.get("Ebs"):
            ebs = mapping["Ebs"].copy()
            ebs["VolumeSize"] = volume_size
            logger.info("Overriding root volume %s size to %d GB", root_device, volume_size)
            return [{"DeviceName": root_device, "Ebs": ebs}]
    raise RuntimeError("Root device mapping not found for AMI %s" % ami_id)


def run_build(
    site_config_path: Path,
    build_config_path: Path,
    bootstrap_path: Optional[Path] = None,
    volume_size: Optional[int] = None,
):
    """
    Execute the build workflow using the provided configuration paths.
    """
    site_config_path = site_config_path.expanduser().resolve()
    build_config_path = build_config_path.expanduser().resolve()
    site_config = load_site_config(site_config_path)
    build_config = load_build_config(build_config_path)
    apply_config(site_config, build_config)

    logger.info("Starting build using profile=%s, region=%s", PROFILE_NAME, REGION)

    session = get_session()
    ec2_client = session.client("ec2")
    ec2_resource = session.resource("ec2")
    ssm_client = session.client("ssm")

    vpc_id = get_default_vpc_id(ec2_client)
    sg_id = get_or_create_security_group(ec2_client, vpc_id)
    ami_id = resolve_ami_id(ssm_client)

    user_data = load_bootstrap(bootstrap_path)

    logger.info("Launching EC2 instance...")
    instance_kwargs = dict(
        ImageId=ami_id,
        InstanceType=INSTANCE_TYPE,
        KeyName=KEY_NAME,
        IamInstanceProfile={"Name": INSTANCE_PROFILE},
        MinCount=1,
        MaxCount=1,
        SecurityGroupIds=[sg_id],
        TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": INSTANCE_NAME},
                        {"Key": "OwnerProfile", "Value": PROFILE_NAME},
                    ],
                }
            ],
    )
    if user_data:
        instance_kwargs["UserData"] = user_data
    if volume_size:
        instance_kwargs["BlockDeviceMappings"] = build_block_device_mappings(ec2_client, ami_id, volume_size)
    try:
        resp = ec2_client.run_instances(**instance_kwargs)
    except ClientError as e:
        logger.critical("Error launching EC2 instance: %s", e)
        sys.exit(1)

    instance = resp["Instances"][0]
    instance_id = instance["InstanceId"]
    logger.info("Launched instance %s; waiting for it to enter 'running' state...", instance_id)

    inst_res = ec2_resource.Instance(instance_id)
    inst_res.wait_until_running()

    # Reload to get public IP/DNS
    inst_res.load()
    public_ip = inst_res.public_ip_address
    public_dns = inst_res.public_dns_name

    logger.info("Instance is now running.")
    logger.info("  Instance ID: %s", instance_id)
    logger.info("  Public IP:   %s", public_ip)
    logger.info("  Public DNS:  %s", public_dns)

    logger.info("Build complete.")


def main():
    args = parse_args()
    set_logger_level(logger, args.log)
    bootstrap_path = Path(args.bootstrap) if args.bootstrap else None
    run_build(Path(args.site_config), Path(args.build_config), bootstrap_path, args.volume_size)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.critical("Unhandled exception in build: %s", exc)
        sys.exit(1)
