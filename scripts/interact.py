#!/usr/bin/env python3
"""
interact.py

Start an AWS Systems Manager Session Manager shell for an instance.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from scripts import build as build_script
from utils.shared import resolve_instance_identifier
from utils.configs import DEFAULT_CONFIG_PATH, LOG_LEVEL_CHOICES, LOG_LEVEL_DEFAULT
from utils.logger import configure_logger, normalize_log_level, set_logger_level

logger = configure_logger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Open an SSM Session Manager shell to an EC2 instance.")
    parser.add_argument(
        "--site-config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to site JSON config file (default: %(default)s)",
    )
    parser.add_argument(
        "--instance",
        required=True,
        help="Instance ID or Name tag (same as destroy command)",
    )
    parser.add_argument(
        "--command",
        help="Run a one-off command via SSM instead of starting an interactive shell",
    )
    parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )
    return parser.parse_args()


def start_session(site_config_path: Path, instance_identifier: str):
    config = build_script.load_site_config(site_config_path.expanduser().resolve())
    session = boto3.Session(
        aws_access_key_id=config["aws_access_key_id"],
        aws_secret_access_key=config["aws_secret_access_key"],
        region_name=config["region"],
    )
    ec2_client = session.client("ec2")
    instance_id = resolve_instance_identifier(ec2_client, instance_identifier, logger)

    env = os.environ.copy()
    env.update(
        {
            "AWS_ACCESS_KEY_ID": config["aws_access_key_id"],
            "AWS_SECRET_ACCESS_KEY": config["aws_secret_access_key"],
            "AWS_DEFAULT_REGION": config["region"],
        }
    )

    logger.info("Starting SSM session to %s", instance_id)
    try:
        subprocess.run(
            ["aws", "ssm", "start-session", "--target", instance_id],
            check=True,
            env=env,
        )
    except FileNotFoundError:
        logger.critical("aws CLI not found; install AWS CLI v2 with Session Manager plugin.")
        raise
    except subprocess.CalledProcessError as exc:
        logger.critical("aws ssm start-session exited with status %s", exc.returncode)
        raise


def _wait_for_command(ssm_client, command_id: str, instance_id: str):
    while True:
        try:
            resp = ssm_client.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ssm_client.exceptions.InvocationDoesNotExist:  # type: ignore[attr-defined]
            time.sleep(1)
            continue
        status = resp["Status"]
        if status in {"Pending", "InProgress", "Delayed"}:
            time.sleep(1)
            continue
        if status != "Success":
            raise RuntimeError(resp.get("StandardErrorContent") or f"SSM command failed: {status}")
        return resp.get("StandardOutputContent", "")


def run_command(site_config_path: Path, instance_identifier: str, command: str) -> str:
    config = build_script.load_site_config(site_config_path.expanduser().resolve())
    session = boto3.Session(
        aws_access_key_id=config["aws_access_key_id"],
        aws_secret_access_key=config["aws_secret_access_key"],
        region_name=config["region"],
    )
    ec2_client = session.client("ec2")
    ssm_client = session.client("ssm")

    instance_id = resolve_instance_identifier(ec2_client, instance_identifier, logger)
    resp = ec2_client.describe_instances(InstanceIds=[instance_id])
    platform = resp["Reservations"][0]["Instances"][0].get("Platform", "")

    if platform == "windows":
        document = "AWS-RunPowerShellScript"
        parameters = {"commands": [command]}
    else:
        document = "AWS-RunShellScript"
        parameters = {"commands": [command]}

    logger.info("Running remote command on %s via %s", instance_id, document)
    try:
        resp = ssm_client.send_command(
            InstanceIds=[instance_id],
            DocumentName=document,
            Parameters=parameters,
        )
    except ClientError as exc:
        logger.error("Failed to run command on %s: %s", instance_id, exc)
        raise

    command_id = resp["Command"]["CommandId"]
    return _wait_for_command(ssm_client, command_id, instance_id)


def main():
    args = parse_args()
    set_logger_level(logger, args.log)
    try:
        if args.command:
            output = run_command(Path(args.site_config), args.instance, args.command)
            if output:
                print(output)
        else:
            start_session(Path(args.site_config), args.instance)
    except Exception as exc:
        logger.critical("Failed to start session: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
