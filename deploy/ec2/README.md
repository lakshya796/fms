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

The Django admin is at `https://api-test.phloz.app/fms/admin/`, and needs a
login with `is_staff` set. Because nginx strips the `/fms` prefix before
proxying, `DJANGO_SCRIPT_NAME=/fms` must be in `shared/fms.env` — the deploy
script writes it, and adds it to installs that predate it. Without it Django
generates its links, form actions and stylesheet URLs against the domain root,
which is a different application: the admin loads unstyled and can't log in.

Secrets and the production database are intentionally not stored in GitHub.
