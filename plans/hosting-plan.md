# Hosting Plan

This document describes the infrastructure for hosting Track Timing Predictor on AWS.

## Architecture

```
GitHub Actions (CI/CD)
  ├── Build Docker image → ECR
  └── CDK deploy
        ├── TrackTimingBase (shared)
        │     ├── ECR repository
        │     └── GitHub Actions OIDC role
        └── TrackTimingStack-{env} (per-environment)
              ├── DynamoDB tables (durations + palmares)
              ├── Lambda function (Docker image)
              ├── Function URL (IAM auth in prod, public in PR envs)
              ├── CloudWatch log group
              └── [prod only] ACM certificate + CloudFront distribution (ttp.lanyonm.org)
```

### Compute: Lambda + Function URL

The app runs as an AWS Lambda function using a Docker image from ECR. Mangum
adapts the FastAPI ASGI app to the Lambda handler interface.

- **Prod:** Function URL uses `AWS_IAM` auth. CloudFront with Origin Access
  Control (OAC) signs requests to the Function URL, so it can only be accessed
  through CloudFront at `https://ttp.lanyonm.org`.
- **PR environments:** Function URL uses `NONE` auth for direct access (no
  CloudFront).

**Why Lambda over App Runner:** App Runner deployments failed with TCP health
check errors on the `hello-app-runner:latest` test image across multiple
regions, indicating an account-level issue. Lambda avoids the health check
requirement entirely and is simpler to operate.

**Configuration:**
- Memory: 512 MB (allocates ~1/3 vCPU; sufficient for concurrent httpx calls)
- Timeout: 60 seconds (accommodates slow tracktiming.live responses)
- Runtime: Python 3.13 via `public.ecr.aws/lambda/python:3.13`, pinned by digest in the `Dockerfile`; dependencies install from the hashed `requirements.txt` lock with `--require-hashes`

**Known trade-off — in-memory caches:** The prediction algorithm uses
module-level Python dicts for caching heat counts, observed durations, live
heat numbers, and status transitions. On Lambda, these caches persist within a
warm execution environment but reset on cold starts and are not shared across
concurrent environments. This may cause:
- More frequent re-fetching of start lists and result pages
- The wall-clock learning fallback (UPCOMING → COMPLETED transition timing)
  triggering less reliably
- Slightly less accurate predictions during cold starts

This is an accepted trade-off. If prediction quality degrades noticeably,
critical caches could be externalized to DynamoDB.

### Storage: DynamoDB

Each environment has two on-demand tables.

**Durations** (`track-timing-{env}`, `DYNAMODB_TABLE`): learned event durations,
single-table design with partition key `pk` only:

| Item type | pk format | Attributes |
|---|---|---|
| Aggregate duration | `AGGREGATE#<disc>`, `AGGREGATE#<disc>##<gender>`, `AGGREGATE#<disc>#<class>`, `AGGREGATE#<disc>#<class>#<gender>` | `total_minutes` (N), `count` (N) |
| Manual override | `OVERRIDE#<disc>` (same four levels) | `duration_minutes` (N) |
| Observation | `OBS#<comp_id>#<sess_id>#<pos>` | loaded field values; commit marker for idempotent re-load |

**Palmares** (`track-timing-palmares-{env}`, `PALMARES_TABLE`): racer results,
partition key `pk` = `RACER#{name}`, sort key `sk` = `COMP#{id}#S#{sid}#E#{pos}`.

- Billing: on-demand (PAY_PER_REQUEST)
- Prod removal policy: RETAIN (data survives stack deletion)
- PR env removal policy: DESTROY

The app dispatches to DynamoDB when `DYNAMODB_TABLE` / `PALMARES_TABLE` are
set, otherwise falls back to SQLite for local development.

### Container Registry: ECR

A single ECR repository (`track-timing-predictor`) is shared across all
environments. Tags:
- `prod-latest` — rolling tag updated on every main-branch deploy
- `<git-sha>` — immutable tag per deploy for rollback capability

**Lifecycle rules:**
- Untagged images: deleted after 1 day
- All tagged images: keep the 10 most recently pushed (`prod-latest` is
  retagged on every deploy so it is always among the newest)

### Logging and Monitoring: CloudWatch

Each Lambda function gets a dedicated CloudWatch log group:
- Name: `/aws/lambda/track-timing-{env}`
- Retention: 1 month
- Removal policy: DESTROY (log groups cleaned up with stack)

The app emits structured JSON logs via `python-json-logger`.

**Error alarm (prod only):** A CloudWatch alarm triggers when the Lambda
`Errors` metric exceeds 5 in a 5-minute period. The alarm publishes to an
SNS topic (`track-timing-prod-alarms`). To receive email notifications,
subscribe to the topic after the first deploy:

```bash
aws sns subscribe \
  --topic-arn <AlarmTopicArn from stack output> \
  --protocol email \
  --notification-endpoint your-email@example.com
```

Confirm the subscription via the email you receive. The alarm uses
`TreatMissingData.NOT_BREACHING` so periods with no invocations (e.g.,
overnight) do not trigger false alerts.

## IAM Roles

### GitHub Actions OIDC Role

The `track-timing-github-actions` role uses OIDC federation (no long-lived
credentials). It is scoped to the `lanyonm/track-timing-predictor` repository
via the `sub` claim condition.

Trust: the `sub` claim may match any branch (`ref:refs/heads/*`) or any
same-repo pull request.

The role's inline `CdkDeployPolicy` grants only:
- `sts:AssumeRole` on the CDK bootstrap roles (`cdk-hnb659fds-*`)
- read-only CloudFormation describe calls
- ECR auth plus push/pull on the `track-timing-predictor` repository
- `ssm:GetParameter` on the CDK bootstrap version parameter

CloudFormation changes are executed by the CDK bootstrap execution role, which
has the bootstrap default (`AdministratorAccess`). Anyone who can run a workflow
on a branch of this repo can therefore deploy arbitrary infrastructure.

### Lambda Execution Role

CDK auto-generates the Lambda execution role with:
- `AWSLambdaBasicExecutionRole` (CloudWatch Logs)
- DynamoDB read/write scoped to the environment's two tables

## CI/CD

### Production Deploy (`.github/workflows/deploy.yml`)

Triggered on push to `main` (independently of the test workflow; a failing
test run does not block the deploy):
1. Assume OIDC role
2. Build Docker image, push to ECR with SHA tag + `prod-latest`
3. `cdk deploy TrackTimingBase TrackTimingStack-prod --context image_tag=<sha>`

Passing the SHA as `image_tag` context ensures CloudFormation detects the image
change and updates the Lambda function.

### PR Environments (`.github/workflows/pr-environment.yml`)

Triggered on PR open/sync/close against `main`, for PRs from branches in this
repository only (fork PRs are skipped on deploy):
- **open/synchronize:** Build image, push with SHA tag, deploy ephemeral
  `TrackTimingStack-pr-<N>` stack, comment the Function URL on the PR
- **close:** `cdk destroy TrackTimingStack-pr-<N>` tears down all resources

PR stacks use DESTROY removal policies so DynamoDB tables and log groups are
cleaned up automatically.

### Tests (`.github/workflows/test.yml`)

Triggered on push/PR to `main`. Both jobs install the hashed `requirements-dev.txt`
lock. `lint` runs `ruff check`, `ruff format --check` and `mypy`; `test` runs
`pytest` with coverage and, on `main`, publishes the coverage percentage to a gist
for the README badge (`GIST_TOKEN` secret).

All workflow actions are pinned by commit SHA, the CDK CLI by npm version and
`cdk/requirements.txt` by hashed lock. Dependabot (`.github/dependabot.yml`)
proposes monthly updates for pip, GitHub Actions and the base image.

## Local Development

Local dev continues to use uvicorn + SQLite:
```bash
source .venv/bin/activate
pip install --require-hashes -r requirements-dev.txt
uvicorn app.main:app --reload
```

No AWS credentials or DynamoDB setup required — the app defaults to SQLite
when `DYNAMODB_TABLE` is not set.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DYNAMODB_TABLE` | `""` (SQLite mode) | DynamoDB table name; enables DynamoDB backend |
| `PALMARES_TABLE` | `""` (SQLite mode) | Palmares DynamoDB table name; enables DynamoDB palmares backend |
| `AWS_REGION` | `us-east-1` | AWS region for DynamoDB client |
| `DB_PATH` | `timings.db` | SQLite database path (local dev only) |
| `VENUE_TZ` | `America/Toronto` | Fallback venue timezone; the Lambda clock is UTC and the live offset is inferred from result pages (not set by CDK, so the default applies) |
| `PUBLIC_BASE_URL` | `""` | Origin for the palmares share link. Prod CDK sets `https://ttp.lanyonm.org`, because CloudFront forwards the Function URL host; PR envs leave it empty and use the request host |

`PYTHONUNBUFFERED=1` is set in the Dockerfile for immediate log output.

## Cost Estimate

At expected traffic levels (a few concurrent users during race events):
- **Lambda:** Well within free tier (1M requests/month, 400K GB-seconds)
- **DynamoDB:** Well within free tier (25 GB storage, 25 WCU/RCU)
- **ECR:** Minimal storage (~300 MB for 5 images)
- **CloudWatch:** Minimal log volume

### Custom Domain: CloudFront + ACM (prod only)

Prod traffic is served through CloudFront at `https://ttp.lanyonm.org`:
- **ACM certificate:** DNS-validated for `ttp.lanyonm.org`
- **CloudFront distribution:** Origin Access Control (OAC) to the Lambda Function URL
- **Origin request policy:** `ALL_VIEWER_EXCEPT_HOST_HEADER` — required so CloudFront
  doesn't forward its own domain as the `Host` header, which would break SigV4 signing
- **Cache policy:** `CACHING_DISABLED` (the app is dynamic — predictions change
  every 30 seconds)
- **Viewer protocol:** HTTP redirects to HTTPS

**CDK workaround (dual auth):** AWS requires both `lambda:InvokeFunctionUrl` and
`lambda:InvokeFunction` in the Lambda resource policy for CloudFront OAC access.
CDK's `FunctionUrlOrigin.with_origin_access_control()` only grants the first, so
the stack manually adds the second via `fn.add_permission()`. See
[CDK #35872](https://github.com/aws/aws-cdk/issues/35872). This workaround can
be removed once CDK fixes the issue upstream.

DNS is managed at Name.com (not Route53). Two CNAME records are required:
1. ACM validation CNAME (one-time, created during first deploy)
2. `ttp` → CloudFront distribution domain (e.g., `d1234abcdef.cloudfront.net`)

## First Deploy (one-time manual steps)

1. Create the OIDC provider: `aws iam create-open-id-connect-provider` for
   `token.actions.githubusercontent.com`
2. Deploy base stack: `cdk deploy TrackTimingBase` (creates ECR repo + OIDC role)
3. Add `AWS_ACCOUNT_ID` secret to the GitHub repo
4. Deploy prod stack: `cdk deploy TrackTimingStack-prod --context image_tag=prod-latest`
   - The deploy will pause waiting for ACM certificate DNS validation
   - Add the ACM validation CNAME at Name.com (visible in AWS Console →
     Certificate Manager)
   - Wait for validation (2-5 minutes), deploy continues automatically
5. After deploy completes, add the domain CNAME at Name.com:
   `ttp` → the `DistributionDomain` output value
6. Build and push the first Docker image (via CI on merge to main, or manually)

## Not Configured

- **Rate limiting / WAF:** none. The CloudFront distribution and PR Function URLs
  are publicly reachable without throttling, and the Lambda has no reserved
  concurrency limit.
- **Shared caches:** prediction caches are per Lambda container (see above).
