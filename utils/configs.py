"""Shared configuration values for aws_builder utilities."""

from pathlib import Path

# Common logging levels for CLI entry points.
LOG_LEVEL_CHOICES = ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
LOG_LEVEL_DEFAULT = "INFO"

# Default paths to configuration files.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "site_config.json"
DEFAULT_BUILD_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "build_config.json"

# Default behavior for destroy workflow.
DEFAULT_DESTROY_GROUP = True

# Upload (SSM chunk) settings.
# Keep well under SSM's 97 KB document limit; this value is base64 chars (~36 KB raw).
UPLOAD_CHUNK_SIZE = 48_000

# Port forwarding defaults
DEFAULT_TUNNEL_LOCAL_PORT = 22222
DEFAULT_TUNNEL_REMOTE_PORT = 22
