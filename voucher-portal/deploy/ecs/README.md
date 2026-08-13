# Deploying to ECS Fargate

Two containers in one task: `api` (Django/gunicorn, port 8000) and `web`
(Next.js, port 3000). One ALB in front, routing by path.

## Before the first deploy

| Thing | Why |
| --- | --- |
| RDS Postgres | SQLite cannot honour the row lock the voucher numbering allocator uses. Two containers on SQLite will issue duplicate voucher numbers. |
| EFS access point | Uploaded card artwork must outlive a container. Without it, every deploy silently loses the artwork. |
| Secrets Manager entries | `DJANGO_SECRET_KEY` and the DB credentials. Anything in `environment` is readable by anyone who can describe the task. |
| Two ECR repos | `mair-voucher-portal-api` and `mair-voucher-portal-web`. |

## Build and push

The UI's API URL is **compiled into the bundle** — Next inlines `NEXT_PUBLIC_*`
at build time. Point it at the wrong host and no runtime setting will move it;
you have to rebuild.

```bash
ACCOUNT=<ACCOUNT_ID>; REGION=<REGION>; TAG=$(git rev-parse --short HEAD)
REGISTRY=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REGISTRY

docker build -t $REGISTRY/mair-voucher-portal-api:$TAG ./backend
docker build -t $REGISTRY/mair-voucher-portal-web:$TAG \
  --build-arg NEXT_PUBLIC_FMS_API_URL=https://api.vouchers.example.com ./frontend

docker push $REGISTRY/mair-voucher-portal-api:$TAG
docker push $REGISTRY/mair-voucher-portal-web:$TAG
```

## Migrations

**Not in the container start command.** With more than one replica, every
container races to apply the same migration on deploy. Run it once, as a
one-off task, before the service rolls:

```bash
aws ecs run-task --cluster <CLUSTER> --launch-type FARGATE \
  --task-definition mair-voucher-portal \
  --network-configuration 'awsvpcConfiguration={subnets=[<SUBNETS>],securityGroups=[<SG>],assignPublicIp=DISABLED}' \
  --overrides '{"containerOverrides":[{"name":"api","command":["python","manage.py","migrate","--noinput"]}]}'
```

The same shape runs `createsuperuser` (needs `--no-input` plus
`DJANGO_SUPERUSER_*` env) and `seed_voucher_portal`.

## Service and load balancer

Register the task definition, then create the service with two target groups:

| Path | Target | Health check |
| --- | --- | --- |
| `/api/*`, `/admin/*`, `/static/*` | `api:8000` | `/api/v1/health/` |
| everything else | `web:3000` | `/voucher-portal` |

```bash
aws ecs register-task-definition --cli-input-json file://task-definition.json
aws ecs create-service --cluster <CLUSTER> --service-name mair-voucher-portal \
  --task-definition mair-voucher-portal --desired-count 2 --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[<SUBNETS>],securityGroups=[<SG>],assignPublicIp=DISABLED}' \
  --load-balancers \
    targetGroupArn=<API_TG_ARN>,containerName=api,containerPort=8000 \
    targetGroupArn=<WEB_TG_ARN>,containerName=web,containerPort=3000
```

Subsequent deploys are just a new image tag:

```bash
aws ecs update-service --cluster <CLUSTER> --service mair-voucher-portal --force-new-deployment
```

## Things that will bite

- **`DJANGO_ALLOWED_HOSTS`** — the ALB health check reaches the container by
  IP, not by hostname. Leave the `*` or add the VPC CIDR, or Django answers
  400 and ECS kills a perfectly healthy task in a loop.
- **`CORS_ALLOWED_ORIGINS`** — the browser origin serving the UI. Miss it and
  every request fails as a CORS error with a healthy API behind it and nothing
  in the logs to explain why.
- **Artwork on a container filesystem** — without the EFS mount, uploaded card
  artwork disappears on the next deploy. The API reports "the artwork file is
  missing from storage" rather than 500ing, but the file is gone.
- **PDF generation runs in a background thread**, not a queue. A task killed
  mid-generation leaves the batch in `generating`. Drain connections properly
  (ALB deregistration delay ≥ 60s) and prefer scaling up over recycling.
- **`DJANGO_SCRIPT_NAME`** — only set this if a proxy mounts the API under a
  path prefix *and strips it*. Set wrongly, every admin link 404s.
