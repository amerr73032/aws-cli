#!/usr/bin/env python3
"""
tunnel.py

Start an SSM port forwarding session (local <-> remote port) to an instance.
"""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

import boto3

from scripts import build as build_script
from utils.configs import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_TUNNEL_LOCAL_PORT,
    DEFAULT_TUNNEL_REMOTE_PORT,
    LOG_LEVEL_CHOICES,
    LOG_LEVEL_DEFAULT,
)
from utils.logger import configure_logger, normalize_log_level, set_logger_level
from utils.shared import resolve_instance_identifier

logger = configure_logger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Create an SSM port-forwarding tunnel to an instance.")
    parser.add_argument(
        "--site-config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to site JSON config file (default: %(default)s)",
    )
    parser.add_argument(
        "--instance",
        required=True,
        help="Instance ID or Name tag",
    )
    parser.add_argument(
        "--local-port",
        type=int,
        default=DEFAULT_TUNNEL_LOCAL_PORT,
        help="Local port to listen on (default: %(default)s)",
    )
    parser.add_argument(
        "--remote-port",
        type=int,
        default=DEFAULT_TUNNEL_REMOTE_PORT,
        help="Remote instance port to forward to (default: %(default)s)",
    )
    parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )
    return parser.parse_args()


def start_tunnel(site_config_path: Path, instance_identifier: str, local_port: int, remote_port: int):
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

    logger.info(
        "Starting port forwarding from localhost:%d to %s:%d", local_port, instance_id, remote_port
    )
    logger.info("Press Ctrl+C to terminate the tunnel when finished.")
    cmd = [
        "aws",
        "ssm",
        "start-session",
        "--target",
        instance_id,
        "--document-name",
        "AWS-StartPortForwardingSession",
        "--parameters",
        f"portNumber={remote_port},localPortNumber={local_port}",
    ]
    try:
        subprocess.run(cmd, check=True, env=env)
    except FileNotFoundError:
        logger.critical("aws CLI not found; install AWS CLI v2 with the Session Manager plugin.")
        raise
    except subprocess.CalledProcessError as exc:
        logger.critical("aws ssm start-session exited with status %s", exc.returncode)
        raise


def main():
    args = parse_args()
    set_logger_level(logger, args.log)
    try:
        start_tunnel(Path(args.site_config), args.instance, args.local_port, args.remote_port)
    except Exception as exc:
        logger.critical("Failed to start tunnel: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
