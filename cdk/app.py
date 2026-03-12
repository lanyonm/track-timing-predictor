import os

import aws_cdk as cdk

from base_stack import TrackTimingBaseStack
from track_timing_stack import TrackTimingStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

base = TrackTimingBaseStack(app, "TrackTimingBase", env=env)

# Prod stack — always present; SHA tag passed via --context for deploys
prod_image_tag = app.node.try_get_context("image_tag") or "prod-latest"
TrackTimingStack(
    app,
    "TrackTimingStack-prod",
    env_name="prod",
    repo=base.repo,
    image_tag=prod_image_tag,
    env=env,
)

# Ephemeral PR stacks — deployed ad-hoc from CI with --context flags:
#   cdk deploy TrackTimingStack-pr-42 \
#     --context env_name=pr-42 \
#     --context image_tag=<sha>
env_name = app.node.try_get_context("env_name")
image_tag = app.node.try_get_context("image_tag")
if env_name and env_name != "prod":
    TrackTimingStack(
        app,
        f"TrackTimingStack-{env_name}",
        env_name=env_name,
        repo=base.repo,
        image_tag=image_tag,
        env=env,
    )

app.synth()
