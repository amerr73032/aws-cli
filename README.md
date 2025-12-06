# AWS-CLI

AWS-CLI lets you build, inspect, access, manage, and tear down EC2 instances entirely from one Python entrypoint—no AWS console needed.

## Features

- **Build automation** – Launches any AMI (Linux or Windows) with whatever security-group rules and bootstrap script you supply.
- **Site inspection** – Uses STS/EC2 APIs to list running instances, VPC info, and public endpoints per site config.
- **Session Manager access** – Opens SSM shells or forwards commands without exposing SSH (as long as your AMI/bootstraps install and start the SSM agent).
- **File uploads** – Uploads local files/directories to the instance via SSM Run Command (base64 chunks) with no S3 dependency.
- **Targeted teardown** – Terminates instances by ID or `Name` tag and optionally deletes their security groups.
- **Central logging** – `--log` flag sets the level once so every script (build/show/upload/destroy/interact) emits consistent info/debug output.

## Workflow highlights

- Bring a host to life (`build`) with configs and (optionally) a bootstrap script that installs packages, enables the SSM agent, or customizes the OS.
- Inspect the environment (`show_site`) to confirm credentials, default VPC, and running assets before deploying payloads.
- Open an interactive shell (`interact`) via Session Manager without juggling SSH keys, bastions, or security-group changes.
- Push artifacts (`upload`) directly to `/home/ssm-user` (or any path) even when outbound internet is blocked—SSM streams base64 chunks and reassembles the file remotely.
- Clean up (`destroy`) by instance ID or `Name` tag; pass `--destroy-group true/false` to remove or keep the SG.

## Supported commands

- `build` – launches EC2 instances using `configs/site_config.json` (credentials/region) and `configs/build_config.json` (instance parameters). Supports `--bootstrap path/to/script.sh`.
  Use `--volume-size` to override the root disk size (in GB) if the AMI’s default isn’t large enough.
- `show_site` – displays metadata for the site config: default VPC, caller identity, instances, and last-seen public IP.
- `interact` – starts an SSM shell on a target instance (`--instance i-12345` or `--instance my-tag`) or runs a one-off command via `--command "uname -a"`.
- `upload` – streams a file or tarred directory to the instance; specify `--source` and `--destination` (defaults: `~/` → `~`).
- `manage` – starts, stops, or shows details for an existing instance without rebuilding it.
- `tunnel` – forwards a local TCP port to a remote instance port via SSM (useful for `scp`, `rsync`, RDP, etc., without opening security-group ingress).
- `destroy` – terminates an instance and optionally deletes its security group (`--destroy-group true` by default).

## Behavioral knobs

- `--log` – per-command log level (`CRITICAL`…`DEBUG`).
- `--bootstrap` – shell script executed as EC2 user data; perfect for installing packages, enabling SSM, or configuring services.
- `--site-config` / `--build-config` – point at alternate JSON files if you maintain multiple lab presets.
- `--destroy-group` – keep (`false`) or remove (`true`, default) the security group during teardown.
- `--volume-size` (build) – override the root EBS volume size when launching.
- `--source` / `--destination` (upload) – choose arbitrary local paths and remote targets; paths expand `~` automatically.

## Requirements

- Python 3.9+ (boto3 target).  
- AWS credentials with EC2/SSM defined in `configs/site_config.json`.
- IAM instance profile (e.g., `AmazonSSMManagedInstanceCore`) referenced in `configs/build_config.json`.
- Session Manager plugin installed locally to enable `aws ssm start-session`.

## Installation & quickstart

```bash
git clone https://github.com/WWT/atc-cyber-range-scripts.git
cd atc-cyber-range-scripts/CR17_code_blue/aws_builder
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python aws_cli.py --help
```

Drop your site/build configs into `configs/`, then run `python aws_cli.py build --site-config configs/site_config.json --build-config configs/build_config.json`.

## Command structure

```bash
python aws_cli.py
# inside the prompt
aws> build --site-config configs/site_config.json --build-config configs/build_config.json --bootstrap configs/bootstrap.sh
aws> show_site --config configs/site_config.json --log debug
aws> interact --instance lin-test
aws> upload --instance lin-test --source ~/scripts/setup.sh --destination /home/ssm-user/setup.sh
aws> manage --instance lin-test --show
aws> destroy --instance lin-test --destroy-group false
aws> tunnel --instance lin-test --local-port 22222 --remote-port 22
aws> quit
```

You can also run any command directly without entering the prompt:

```bash
python aws_cli.py build --site-config configs/site_config.json --build-config configs/build_config.json
python aws_cli.py interact --instance lin-test --command "ip addr"
python aws_cli.py manage --instance lin-test --start
python aws_cli.py tunnel --instance lin-test --local-port 22222 --remote-port 22
```

The prompt wraps `scripts/build.py`, `scripts/show_site.py`, `scripts/interact.py`, `scripts/upload.py`, and `scripts/destroy.py`. Each command reads the shared configs, normalizes logging, and reports status inline.

## Config structure

### `configs/site_config.json`
```json
{
  "profile_name": "site0",             # Friendly name shows up in AWS CLI prompts/logs; make it unique per environment.
  "region": "us-east-2",               # Default AWS region where builders launch instances (match your lab's AMIs).
  "aws_access_key_id": "...",          # IAM user's access key with EC2/SSM permissions if not using an existing CLI profile.
  "aws_secret_access_key": "..."       # Matching secret for the same IAM access key; keep this file secure.
}
```
Defines the AWS account/region plus inline credentials used throughout the CLI.

### `configs/build_config.json`
```json
{
  "instance_type": "t3.micro",         # EC2 shape; adjust for CPU/memory needs (match Windows/Linux requirements).
  "key_name": "your-keypair",          # Optional SSH key pair to inject for AMIs that still rely on SSH (not used by SSM).
  "security_group_name": "demo-sg",    # Security group tag to create/use for ingress rules defined in the build script.
  "instance_name": "lin-test",         # Name tag applied to the instance and used as shorthand across interact/manage/destroy.
  "instance_profile": "ssm-role",      # IAM instance profile to attach so the VM can register with SSM (e.g., AmazonSSMManagedInstanceCore).
  "ami_id": "ami-0c5ddb3560e768732"    # AMI to launch; ensure it exists in the chosen region and has/installs the SSM agent.
}
```
Captures EC2 parameters (AMI, instance type, SG name, IAM role). Bootstrap scripts live outside the config and are passed via `--bootstrap`.

## Example bootstrap script

```bash
#!/bin/bash
set -euo pipefail

apt-get update -y
apt-get install -y snapd docker.io

if ! snap list | grep -q amazon-ssm-agent; then
  snap install amazon-ssm-agent --classic
fi
systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent
```

Place it anywhere (e.g., `configs/bootstrap.sh`) and pass `--bootstrap configs/bootstrap.sh` when running `build`.

## Site inspection via `show_site`

- Quick sanity check for credentials, account alias, default VPC, and currently running instances: `python aws_cli.py show_site --config configs/site_config.json`.
- Use `--log debug` to see the raw caller identity and VPC lookups if something seems off with permissions.
- Accepts alternate site configs so you can verify multiple accounts/regions before kicking off a build.

## Builds via `build`

- Provision instances end-to-end: `python aws_cli.py build --site-config configs/site_config.json --build-config configs/build_config.json --bootstrap configs/bootstrap.sh`.
- `--site-config` handles credentials/region while `--build-config` defines AMI, instance type, SG name, etc.; override either flag to swap environments quickly.
- Pass `--volume-size 100` to enlarge the root disk beyond the AMI default, and `--bootstrap` to run user-data scripts on first boot.
- Watch the log for the new instance ID, public IP/DNS, and any security-group reuse/creation steps before jumping into `interact`.

## Interactive shells via `interact`

- Launch an SSM Session Manager shell without SSH keys or inbound rules: `python aws_cli.py interact --instance lin-test`.
- Run single commands instead of a shell by passing `--command "uname -a"`; output streams directly to your terminal.
- Instances are looked up by Name tag (from `instance_name`), so `--instance lin-test` works across `interact`, `manage`, and `destroy`.
- Add `--log debug` when troubleshooting to view the underlying SSM task IDs and responses.

## Lifecycle controls via `manage`

- Start/stop and inspect existing instances without rebuilding: `python aws_cli.py manage --instance lin-test --show|--start|--stop`.
- `--show` prints state, IPs, key name, security groups, instance-type specs, and current volume sizing.
- `--start` and `--stop` block until the instance reaches the requested state (helpful before running `interact` or `destroy`).
- Combine with Name tags or explicit instance IDs: `--instance i-0123456789abcdef0` works when tags are missing.

## File transfers via `upload`

- Directories are tarred automatically; files stream as-is.
- Chunks are base64-encoded and appended to `/tmp/upload-XXXX.bin` via SSM.
- After the last chunk, the temp file is moved or extracted into the requested destination (tilde paths expand correctly).
- Keep uploads under ~5 MB when possible—SSM Run Command chunks cap at ~40 KB, so larger payloads take ages and risk timeouts; use `tunnel` for bulk transfers instead.

## Port forwarding via `tunnel`

- Bounce any TCP service (SSH, RDP, WinRM, file copy tools) through Session Manager without editing security groups or opening public ports.
- Kick it off with something like `python aws_cli.py tunnel --instance lin-test --local-port 2222 --remote-port 22`.
- Point your client at the forwarded port, e.g., `scp -P 2222 large.iso ssm-user@127.0.0.1:/home/ssm-user` or `mstsc /v:127.0.0.1:2222`.
- Close the tunnel once finished to release the SSM session.

## Tear downs via `destroy`

- `python aws_cli.py destroy --instance lin-test --destroy-group true|false` terminates the VM and optionally removes its security group.
- Waits for `shutting-down`/`terminated` states before exiting, so follow-up builds/interacts don’t race existing resources.
- Accepts Name tags or raw instance IDs, letting you surgically clean up multiple hosts without touching others.

## Tips

- Keep `aws_cli.py` running inside a virtual environment so `boto3`, `botocore`, and `cmd2` stay consistent.
- For named instances (`--instance lin-test`), ensure the EC2 Name tag matches across builds to simplify `interact` and `destroy`.
- Use `--log debug` on any command to trace API calls and detailed SSM outputs when troubleshooting.

With these building blocks you can stand up, configure, interact with, manage, and tear down lab infrastructure from a single CLI loop without leaving the terminal.
