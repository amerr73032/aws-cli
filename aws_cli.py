#!/usr/bin/env python3
"""Interactive AWS helper CLI built on cmd2."""

import shlex
from pathlib import Path

import cmd2
from argparse import ArgumentTypeError

from scripts import build as build_script
from scripts import destroy as destroy_script
from scripts import interact as interact_script
from scripts import manage as manage_script
from scripts import tunnel as tunnel_script
from scripts import show_site as show_site_script
from scripts import upload as upload_script
from utils.configs import (
    DEFAULT_BUILD_CONFIG_PATH,
    DEFAULT_CONFIG_PATH,
    DEFAULT_DESTROY_GROUP,
    DEFAULT_TUNNEL_LOCAL_PORT,
    DEFAULT_TUNNEL_REMOTE_PORT,
    LOG_LEVEL_CHOICES,
    LOG_LEVEL_DEFAULT,
)
from utils.logger import normalize_log_level, set_logger_level


def str_to_bool(value: str) -> bool:
    """Convert CLI text to boolean."""
    value = value.lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ArgumentTypeError(f"Invalid boolean value: {value}")


def configure_logging(level: str):
    """Set logging level across our modules."""
    set_logger_level(build_script.logger, level)
    set_logger_level(destroy_script.logger, level)
    set_logger_level(interact_script.logger, level)
    set_logger_level(manage_script.logger, level)
    set_logger_level(tunnel_script.logger, level)
    set_logger_level(upload_script.logger, level)
    set_logger_level(show_site_script.logger, level)


class AwsCli(cmd2.Cmd):
    """Simple cmd2 shell for orchestrating AWS helper workflows."""

    prompt = "aws> "

    def __init__(self):
        super().__init__()
        self.intro = "Type help or ? to list commands."
        self.hidden_commands = [
            "alias",
            "edit",
            "macro",
            "run_pyscript",
            "run_script",
            "set",
            "shell",
            "shortcuts",
            "_relative_run_script",
            "eof",
        ]

    build_parser = cmd2.Cmd2ArgumentParser(description="Provision an EC2 host from configs")
    build_parser.add_argument(
        "--site-config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to site JSON config file (default: %(default)s)",
    )
    build_parser.add_argument(
        "--build-config",
        default=str(DEFAULT_BUILD_CONFIG_PATH),
        help="Path to build JSON config file (default: %(default)s)",
    )
    build_parser.add_argument(
        "--bootstrap",
        help="Path to bootstrap script to run on first boot",
    )
    build_parser.add_argument(
        "--volume-size",
        type=int,
        help="Override root volume size (GB)",
    )
    build_parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )

    @cmd2.with_argparser(build_parser)
    def do_build(self, args):
        """Run the EC2 build workflow using scripts/build.py."""

        site_config_path = Path(args.site_config)
        build_config_path = Path(args.build_config)
        bootstrap_path = Path(args.bootstrap) if args.bootstrap else None
        configure_logging(args.log)
        try:
            build_script.run_build(site_config_path, build_config_path, bootstrap_path, args.volume_size)
        except Exception as exc:  # cmd2 handles logging to user via perror
            self.perror(f"Build failed: {exc}")
        else:
            self.poutput("Build completed successfully.")

    show_site_parser = cmd2.Cmd2ArgumentParser(description="Show AWS site connection details")
    show_site_parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to JSON config file (default: %(default)s)",
    )
    show_site_parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )

    @cmd2.with_argparser(show_site_parser)
    def do_show_site(self, args):
        """Display site connectivity info (public IP, instances, etc.)."""

        config_path = Path(args.config)
        configure_logging(args.log)
        try:
            result = show_site_script.run_show_site(config_path)
        except Exception as exc:
            self.perror(f"show_site failed: {exc}")
        else:
            self.poutput(show_site_script.format_result(result))

    destroy_parser = cmd2.Cmd2ArgumentParser(description="Terminate an EC2 instance")
    destroy_parser.add_argument(
        "--site-config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to site JSON config file (default: %(default)s)",
    )
    destroy_parser.add_argument(
        "--instance",
        required=True,
        help="Instance ID or Name tag to terminate",
    )
    destroy_parser.add_argument(
        "--destroy-group",
        default=DEFAULT_DESTROY_GROUP,
        type=str_to_bool,
        help="Delete the security group referenced in the build config (default: %(default)s)",
    )
    destroy_parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )

    @cmd2.with_argparser(destroy_parser)
    def do_destroy(self, args):
        """Terminate an EC2 instance and optionally delete its security group."""

        site_config_path = Path(args.site_config)
        configure_logging(args.log)
        try:
            destroy_script.run_destroy(
                site_config_path,
                args.instance,
                args.destroy_group,
            )
        except Exception as exc:
            self.perror(f"Destroy failed: {exc}")
        else:
            self.poutput("Destroy completed successfully.")

    interact_parser = cmd2.Cmd2ArgumentParser(description="Start an SSM shell session on an instance")
    interact_parser.add_argument(
        "--site-config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to site JSON config file (default: %(default)s)",
    )
    interact_parser.add_argument(
        "--instance",
        required=True,
        help="Instance ID or Name tag",
    )
    interact_parser.add_argument(
        "--command",
        help="Run a single command via SSM instead of an interactive shell",
    )
    interact_parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )

    @cmd2.with_argparser(interact_parser)
    def do_interact(self, args):
        """Drop into an SSM Session Manager shell for the target instance."""

        site_config_path = Path(args.site_config)
        configure_logging(args.log)
        try:
            if args.command:
                output = interact_script.run_command(site_config_path, args.instance, args.command)
                if output:
                    self.poutput(output)
            else:
                interact_script.start_session(site_config_path, args.instance)
        except Exception as exc:
            self.perror(f"Interact failed: {exc}")
        else:
            self.poutput("Session ended.")

    upload_parser = cmd2.Cmd2ArgumentParser(description="Upload files or directories to an EC2 instance via SSM")
    upload_parser.add_argument(
        "--site-config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to site JSON config file (default: %(default)s)",
    )
    upload_parser.add_argument(
        "--instance",
        required=True,
        help="Instance ID or Name tag",
    )
    upload_parser.add_argument(
        "--source",
        default=str(Path.home()),
        help="Local file or directory to upload (default: %(default)s)",
    )
    upload_parser.add_argument(
        "--destination",
        default="~",
        help="Destination directory on the instance (default: %(default)s)",
    )
    upload_parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )

    @cmd2.with_argparser(upload_parser)
    def do_upload(self, args):
        """Upload a local file or directory to an instance via SSM."""

        site_config_path = Path(args.site_config)
        source_path = Path(args.source).expanduser().resolve()
        configure_logging(args.log)
        try:
            upload_script.run_upload(
                site_config_path,
                args.instance,
                source_path,
                args.destination,
            )
        except Exception as exc:
            self.perror(f"Upload failed: {exc}")
        else:
            self.poutput("Upload completed successfully.")

    manage_parser = cmd2.Cmd2ArgumentParser(description="Start or stop an EC2 instance")
    manage_parser.add_argument(
        "--site-config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to site JSON config file (default: %(default)s)",
    )
    manage_parser.add_argument(
        "--instance",
        required=True,
        help="Instance ID or Name tag",
    )
    manage_parser.add_argument(
        "--start",
        action="store_true",
        help="Start the specified instance",
    )
    manage_parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop the specified instance",
    )
    manage_parser.add_argument(
        "--show",
        action="store_true",
        help="Show instance details",
    )
    manage_parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )

    @cmd2.with_argparser(manage_parser)
    def do_manage(self, args):
        """Start or stop an EC2 instance."""

        site_config_path = Path(args.site_config)
        configure_logging(args.log)
        try:
            manage_script.run_manage(site_config_path, args.instance, args.start, args.stop, args.show)
        except Exception as exc:
            self.perror(f"Manage failed: {exc}")

    tunnel_parser = cmd2.Cmd2ArgumentParser(description="Start an SSM port-forwarding tunnel")
    tunnel_parser.add_argument(
        "--site-config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to site JSON config file (default: %(default)s)",
    )
    tunnel_parser.add_argument(
        "--instance",
        required=True,
        help="Instance ID or Name tag",
    )
    tunnel_parser.add_argument(
        "--local-port",
        type=int,
        default=DEFAULT_TUNNEL_LOCAL_PORT,
        help="Local port to listen on (default: %(default)s)",
    )
    tunnel_parser.add_argument(
        "--remote-port",
        type=int,
        default=DEFAULT_TUNNEL_REMOTE_PORT,
        help="Remote instance port to forward to (default: %(default)s)",
    )
    tunnel_parser.add_argument(
        "--log",
        default=LOG_LEVEL_DEFAULT,
        choices=LOG_LEVEL_CHOICES,
        type=normalize_log_level,
        help="Logging level (default: %(default)s)",
    )

    @cmd2.with_argparser(tunnel_parser)
    def do_tunnel(self, args):
        """Create a local port forwarding tunnel to an instance via SSM."""

        site_config_path = Path(args.site_config)
        configure_logging(args.log)
        try:
            tunnel_script.start_tunnel(
                site_config_path,
                args.instance,
                args.local_port,
                args.remote_port,
            )
        except Exception as exc:
            self.perror(f"Tunnel failed: {exc}")



def main():
    import sys

    app = AwsCli()
    if len(sys.argv) > 1:
        command = shlex.join(sys.argv[1:])
        app.onecmd(command)
    else:
        app.cmdloop()


if __name__ == "__main__":
    main()
