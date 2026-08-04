# EC2 deployment

These files deploy the Django Fleet Management API separately from the existing Django application on the host.

## Requirements

- Amazon Linux EC2 host
- Python 3, Git and Nginx
- DNS and TLS already configured for `api-test.phloz.app`
- Run as `ec2-user` with sudo access

## Deploy

From a checkout of this repository:

```bash
chmod +x deploy/ec2/deploy-fms.sh
./deploy/ec2/deploy-fms.sh
```

The script creates timestamped releases under `/opt/phloz/fms/releases`, keeps the SQLite database and environment file under `/opt/phloz/fms/shared`, updates the `current` symlink, installs the systemd service, restarts only `phloz-fms`, and verifies its health endpoint.

To deploy another branch:

```bash
FMS_BRANCH=my-branch ./deploy/ec2/deploy-fms.sh
```

## Nginx

Add the contents of `nginx-fms.conf` to the HTTPS server block for `api-test.phloz.app`, validate the configuration, and reload Nginx.

The public API base URL is:

```
https://api-test.phloz.app/fms/api/v1/
```

Secrets and the production database are intentionally not stored in GitHub.
