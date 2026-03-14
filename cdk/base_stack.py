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
            image_scan_on_push=True,
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
        # CDK bootstrap roles carry the permissions to manage CloudFormation stacks
        # and resources; this role only needs to assume them plus push to ECR.
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
                        "token.actions.githubusercontent.com:sub": [
                            "repo:lanyonm/track-timing-predictor:ref:refs/heads/*",
                            "repo:lanyonm/track-timing-predictor:pull_request",
                        ],
                    },
                },
            ),
            inline_policies={
                "CdkDeployPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="AssumeCdkBootstrapRoles",
                            actions=["sts:AssumeRole"],
                            resources=[
                                f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-*-role-{self.account}-us-east-1",
                            ],
                        ),
                        iam.PolicyStatement(
                            sid="CloudFormationDescribe",
                            actions=[
                                "cloudformation:DescribeStacks",
                                "cloudformation:DescribeStackEvents",
                                "cloudformation:GetTemplate",
                                "cloudformation:GetTemplateSummary",
                            ],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            sid="EcrAuthToken",
                            actions=["ecr:GetAuthorizationToken"],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            sid="EcrPushAppImage",
                            actions=[
                                "ecr:BatchCheckLayerAvailability",
                                "ecr:GetDownloadUrlForLayer",
                                "ecr:BatchGetImage",
                                "ecr:PutImage",
                                "ecr:InitiateLayerUpload",
                                "ecr:UploadLayerPart",
                                "ecr:CompleteLayerUpload",
                                "ecr:DescribeRepositories",
                                "ecr:ListImages",
                            ],
                            resources=[
                                f"arn:aws:ecr:us-east-1:{self.account}:repository/track-timing-predictor",
                            ],
                        ),
                        iam.PolicyStatement(
                            sid="SsmBootstrapVersion",
                            actions=["ssm:GetParameter"],
                            resources=[
                                f"arn:aws:ssm:us-east-1:{self.account}:parameter/cdk-bootstrap/hnb659fds/version",
                            ],
                        ),
                    ]
                ),
            },
        )
