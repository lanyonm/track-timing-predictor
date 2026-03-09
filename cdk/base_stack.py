from aws_cdk import (
    Duration,
    Stack,
    aws_ecr as ecr,
    aws_iam as iam,
)
from constructs import Construct


class TrackTimingBaseStack(Stack):
    """Shared resources deployed once: ECR repository and GitHub Actions OIDC role."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ECR — shared across all environments; lifecycle rules prevent unbounded growth
        self.repo = ecr.Repository(
            self,
            "Repo",
            repository_name="track-timing-predictor",
            lifecycle_rules=[
                ecr.LifecycleRule(
                    tag_status=ecr.TagStatus.UNTAGGED,
                    max_image_age=Duration.days(1),
                ),
                # Keep the 10 most recently pushed images. prod-latest is
                # retagged on every deploy so it is always among the newest.
                ecr.LifecycleRule(
                    tag_status=ecr.TagStatus.ANY,
                    max_image_count=10,
                ),
            ],
        )

        # GitHub Actions OIDC role — scoped to this repo only via OIDC subject claim.
        # AdministratorAccess is used because CDK synthesizes IAM roles, policies, and
        # diverse resource types that are impractical to predict and scope in advance.
        # Accepted risk: a compromised workflow could escalate within this account.
        # TODO: Replace with a scoped policy covering CloudFormation, Lambda, DynamoDB,
        #       ECR, IAM (path-scoped), CloudWatch Logs, and STS.
        self.github_actions_role = iam.Role(
            self,
            "GitHubActionsRole",
            role_name="track-timing-github-actions",
            assumed_by=iam.WebIdentityPrincipal(
                f"arn:aws:iam::{self.account}:oidc-provider/token.actions.githubusercontent.com",
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    },
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": "repo:lanyonm/track-timing-predictor:*",
                    },
                },
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess"),
            ],
        )
