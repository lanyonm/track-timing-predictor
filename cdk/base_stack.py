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
                # Expire old SHA-tagged images but never touch prod-latest.
                # ECR lifecycle rules use tag prefix matching; SHA tags are
                # 40-char hex strings starting with 0-9 or a-f.
                ecr.LifecycleRule(
                    tag_prefix_list=[
                        "0", "1", "2", "3", "4", "5", "6", "7",
                        "8", "9", "a", "b", "c", "d", "e", "f",
                    ],
                    max_image_count=5,
                ),
            ],
        )

        # GitHub Actions OIDC role — scoped to this repo only; AdministratorAccess
        # for CDK deployments (see hosting-plan.md IAM Roles section for rationale)
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
