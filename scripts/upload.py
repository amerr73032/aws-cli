#!/usr/bin/env python3
"""
upload.py

Upload a local file or directory to an EC2 instance using SSM Run Command.
The file is optionally archived, base64-encoded, and streamed in chunks to
the remote host where it is reconstructed.
"""

import argparse
import base64
import math
import shlex
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Iterator, Optional, Tuple

import boto3

from scripts import build as build_script
from utils.configs import (
    DEFAULT_CONFIG_PATH,
    LOG_LEVEL_CHOICES,
    LOG_LEVEL_DEFAULT,
    UPLOAD_CHUNK_SIZE,
)
from utils.logger import configure_logger, normalize_log_level, set_logger_level
from utils.shared import resolve_instance_identifier

logger = configure_logger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Upload files or directories to an EC2 instance via SSM.")
    parser.add_argument(
        "--site-config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to site JSON config file (default: %(default)s)",
    )
    parser.add_argument(
        "--instance",
        required=True,
        help="Instance ID or Name tag to upload to.",
    )
    parser.add_argument(
        "--source",
        default=str(Path.home()),
        help="Local file or directory to upload (default: %(default)s)",
    )
    parser.add_argument(
        "--destination",
        default="~",
        help="Destination path on the instance (default: %(default)s)",
    )
    parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )
    return parser.parse_args()


def package_source(source_path: Path) -> Tuple[Path, bool, str]:
    if source_path.is_dir():
        temp_dir = Path(tempfile.mkdtemp())
        base_name = temp_dir / source_path.name
        logger.debug("Packaging directory %s into archive %s.tar.gz", source_path, base_name)
        shutil.make_archive(
            base_name=str(base_name),
            format="gztar",
            root_dir=str(source_path),
            base_dir=".",
        )
        archive_path = Path(f"{base_name}.tar.gz")
        return archive_path, True, source_path.name
    logger.debug("Using file %s for upload (no packaging needed)", source_path)
    return source_path, False, source_path.name


def read_chunks(path: Path) -> Iterator[str]:
    chunk_bytes = UPLOAD_CHUNK_SIZE // 4 * 3
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_bytes)
            if not block:
                break
            encoded = base64.b64encode(block).decode("ascii")
            yield encoded


def send_command(ssm_client, instance_id: str, commands: list[str]):
    resp = ssm_client.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands},
    )
    command_id = resp["Command"]["CommandId"]
    logger.debug("SSM command %s issued to %s", command_id, instance_id)
    while True:
        try:
            resp = ssm_client.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ssm_client.exceptions.InvocationDoesNotExist:  # type: ignore[attr-defined]
            time.sleep(1)
            continue
        status = resp["Status"]
        if status in {"InProgress", "Pending", "Delayed"}:
            time.sleep(1)
            continue
        if status != "Success":
            raise RuntimeError(f"SSM command failed ({status}): {resp.get('StandardErrorContent')}")
        return resp.get("StandardOutputContent")


def stream_chunks(ssm_client, instance_id: str, chunks: Iterator[str], remote_path: str):
    temp_file = f"/tmp/upload-{uuid.uuid4().hex}.bin"
    logger.debug("Remote temp file: %s", temp_file)
    # initialize temp file
    send_command(ssm_client, instance_id, [f": > {shlex.quote(temp_file)}"])

    total_chunks = 0
    for idx, chunk in enumerate(chunks, start=1):
        total_chunks = idx
        command = [
            f"CHUNK='{chunk}'",
            f"echo \"$CHUNK\" | base64 -d >> {shlex.quote(temp_file)}",
        ]
        send_command(ssm_client, instance_id, command)
        logger.info("Uploaded chunk %d", idx)

    logger.info("Transferred %d chunks to %s", total_chunks, instance_id)
    return temp_file


def finalize_remote(
    ssm_client,
    instance_id: str,
    temp_file: str,
    destination: str,
    is_archive: bool,
    original_name: str,
):
    dest_raw = shlex.quote(destination)
    commands = [
        f"DEST_RAW={dest_raw}",
        'case "$DEST_RAW" in',
        '  "~") DEST="$HOME" ;;',
        '  "~/"*) DEST="$HOME/${DEST_RAW#~/}" ;;',
        '  *) DEST="$DEST_RAW" ;;',
        "esac",
        'if [ -d "$DEST" ]; then',
        '  DEST_DIR="$DEST"',
        f'  DEST_FILE={shlex.quote(original_name)}',
        "else",
        '  DEST_DIR="$(dirname \"$DEST\")"',
        '  DEST_FILE="$(basename \"$DEST\")"',
        "fi",
        'mkdir -p "$DEST_DIR"',
    ]
    if is_archive:
        commands.extend(
            [
                f"tar -xzf {shlex.quote(temp_file)} -C \"$DEST_DIR\"",
                f"rm -f {shlex.quote(temp_file)}",
            ]
        )
    else:
        commands.extend(
            [
                f"mv {shlex.quote(temp_file)} \"$DEST_DIR/$DEST_FILE\"",
            ]
        )
    send_command(ssm_client, instance_id, commands)


def run_upload(site_config_path: Path, instance_id_or_name: str, source: Path, destination: str):
    site_config = build_script.load_site_config(site_config_path.expanduser().resolve())
    session = boto3.Session(
        aws_access_key_id=site_config["aws_access_key_id"],
        aws_secret_access_key=site_config["aws_secret_access_key"],
        region_name=site_config["region"],
    )
    ec2_client = session.client("ec2")
    ssm_client = session.client("ssm")
    instance_id = resolve_instance_identifier(ec2_client, instance_id_or_name, logger)

    packaged_path, is_archive, original_name = package_source(source)
    try:
        temp_file = stream_chunks(ssm_client, instance_id, read_chunks(packaged_path), destination)
        finalize_remote(ssm_client, instance_id, temp_file, destination, is_archive, original_name)
        logger.info("Upload completed successfully.")
    finally:
        if packaged_path != source and packaged_path.exists():
            shutil.rmtree(packaged_path.parent, ignore_errors=True)


def main():
    args = parse_args()
    set_logger_level(logger, args.log)
    source_path = Path(args.source).expanduser().resolve()
    if not source_path.exists():
        logger.critical("Source path %s does not exist", source_path)
        sys.exit(1)
    try:
        run_upload(Path(args.site_config), args.instance, source_path, args.destination)
    except Exception as exc:
        logger.critical("Upload failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
