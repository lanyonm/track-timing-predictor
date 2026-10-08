from aws_cdk import (
    Duration,
    Stack,
    aws_ecr as ecr,
    aws_iam as iam,
)
from constructs import Construct

REPO = "lanyonm/track-timing-predictor"
OIDC_PROVIDER = "token.actions.githubusercontent.com"
PR_BOUNDARY_NAME = "track-timing-pr-boundary"
PR_ASSET_PREFIX = "pr/"


class TrackTimingBaseStack(Stack):
    """Shared resources: ECR repository and the GitHub Actions OIDC roles.

    Two roles, split by OIDC subject:
      - track-timing-github-actions (prod): only jobs in the `production` GitHub
        environment, which only main may deploy to. Assumes the CDK bootstrap roles.
      - track-timing-github-actions-pr: pull_request jobs and main-branch jobs (the PR
        stack sweeper). PR stacks deploy with this role's own credentials
        (CliCredentialsStackSynthesizer), so its policy alone bounds what a PR workflow
        can touch: TrackTimingStack-pr-* stacks and track-timing-pr-* resources, with a
        permissions boundary on every IAM role it creates.
    """

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
                # PR images (pr-<N>-<sha>) get their own count so they can't
                # push prod rollback images out.
                ecr.LifecycleRule(
                    tag_status=ecr.TagStatus.TAGGED,
                    tag_prefix_list=["pr-"],
                    max_image_count=5,
                ),
                # Everything else is prod: <sha> plus prod-latest, which is
                # retagged on every deploy so it is always among the newest.
                ecr.LifecycleRule(
                    tag_status=ecr.TagStatus.ANY,
                    max_image_count=10,
                ),
            ],
        )

        ecr_push = iam.PolicyStatement(
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
            resources=[self.repo.repository_arn],
        )
        ecr_auth = iam.PolicyStatement(sid="EcrAuthToken", actions=["ecr:GetAuthorizationToken"], resources=["*"])

        # Prod deploy role: CDK bootstrap roles carry the permissions to manage
        # CloudFormation stacks and resources; this role assumes them and pushes to ECR.
        self.github_actions_role = iam.Role(
            self,
            "GitHubActionsRole",
            role_name="track-timing-github-actions",
            assumed_by=_github_oidc_principal(self.account, [f"repo:{REPO}:environment:production"]),
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
                        ecr_auth,
                        ecr_push,
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

        # Permissions boundary for every IAM role a PR stack creates (the Lambda
        # execution role): at most what the PR function needs at runtime.
        pr_boundary = iam.ManagedPolicy(
            self,
            "PrBoundary",
            managed_policy_name=PR_BOUNDARY_NAME,
            statements=[
                iam.PolicyStatement(
                    sid="Logs",
                    actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                    resources=[self._arn("logs", "log-group:/aws/lambda/track-timing-pr-*")],
                ),
                iam.PolicyStatement(
                    sid="Tables",
                    actions=["dynamodb:*"],
                    resources=self._pr_table_arns(),
                ),
                iam.PolicyStatement(
                    sid="PullImage",
                    actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                    resources=[self.repo.repository_arn],
                ),
                ecr_auth,
            ],
        )

        pr_role_arn = self._arn("iam", "role/TrackTimingStack-pr-*", region="")
        bootstrap_bucket = f"cdk-hnb659fds-assets-{self.account}-us-east-1"
        self.github_actions_pr_role = iam.Role(
            self,
            "GitHubActionsPrRole",
            role_name="track-timing-github-actions-pr",
            assumed_by=_github_oidc_principal(
                self.account,
                [f"repo:{REPO}:pull_request", f"repo:{REPO}:ref:refs/heads/main"],
            ),
            inline_policies={
                "PrStackPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="PrStacks",
                            actions=[
                                "cloudformation:CreateStack",
                                "cloudformation:UpdateStack",
                                "cloudformation:DeleteStack",
                                "cloudformation:CreateChangeSet",
                                "cloudformation:DescribeChangeSet",
                                "cloudformation:ExecuteChangeSet",
                                "cloudformation:DeleteChangeSet",
                                "cloudformation:ListChangeSets",
                                "cloudformation:DescribeStackEvents",
                                "cloudformation:DescribeStackResource",
                                "cloudformation:DescribeStackResources",
                                "cloudformation:ListStackResources",
                                "cloudformation:GetTemplate",
                            ],
                            resources=[self._arn("cloudformation", "stack/TrackTimingStack-pr-*/*")],
                        ),
                        iam.PolicyStatement(
                            sid="CloudFormationRead",
                            actions=[
                                "cloudformation:DescribeStacks",
                                "cloudformation:ListStacks",
                                "cloudformation:GetTemplateSummary",
                                "cloudformation:ValidateTemplate",
                                "cloudformation:ListExports",
                            ],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            sid="PrFunctions",
                            actions=["lambda:*"],
                            resources=[self._arn("lambda", "function:track-timing-pr-*")],
                        ),
                        iam.PolicyStatement(
                            sid="PrTables",
                            actions=["dynamodb:*"],
                            resources=self._pr_table_arns(),
                        ),
                        iam.PolicyStatement(
                            sid="PrLogGroups",
                            actions=["logs:*"],
                            resources=[
                                self._arn("logs", "log-group:/aws/lambda/track-timing-pr-*"),
                                self._arn("logs", "log-group:/aws/lambda/track-timing-pr-*:*"),
                            ],
                        ),
                        iam.PolicyStatement(
                            sid="LogsRead",
                            actions=["logs:DescribeLogGroups"],
                            resources=["*"],
                        ),
                        # Roles the PR stack creates must carry the boundary, and may
                        # only attach the basic Lambda execution policy.
                        iam.PolicyStatement(
                            sid="CreatePrRolesWithBoundary",
                            actions=["iam:CreateRole", "iam:PutRolePolicy"],
                            resources=[pr_role_arn],
                            conditions={"StringEquals": {"iam:PermissionsBoundary": pr_boundary.managed_policy_arn}},
                        ),
                        iam.PolicyStatement(
                            sid="AttachBasicExecution",
                            actions=["iam:AttachRolePolicy", "iam:DetachRolePolicy"],
                            resources=[pr_role_arn],
                            conditions={
                                "StringEquals": {
                                    "iam:PermissionsBoundary": pr_boundary.managed_policy_arn,
                                    "iam:PolicyARN": "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                                }
                            },
                        ),
                        iam.PolicyStatement(
                            sid="ManagePrRoles",
                            actions=[
                                "iam:DeleteRole",
                                "iam:DeleteRolePolicy",
                                "iam:GetRole",
                                "iam:GetRolePolicy",
                                "iam:ListRolePolicies",
                                "iam:ListAttachedRolePolicies",
                                "iam:TagRole",
                                "iam:UntagRole",
                            ],
                            resources=[pr_role_arn],
                        ),
                        iam.PolicyStatement(
                            sid="PassPrRolesToLambda",
                            actions=["iam:PassRole"],
                            resources=[pr_role_arn],
                            conditions={"StringEquals": {"iam:PassedToService": "lambda.amazonaws.com"}},
                        ),
                        # The CLI uploads PR templates to the bootstrap bucket. Writes are
                        # limited to pr/: keys are content hashes and the CLI skips existing
                        # ones, so a write elsewhere could plant a future prod template.
                        iam.PolicyStatement(
                            sid="BootstrapBucketRead",
                            actions=["s3:ListBucket", "s3:GetBucketLocation", "s3:GetEncryptionConfiguration"],
                            resources=[f"arn:aws:s3:::{bootstrap_bucket}"],
                        ),
                        iam.PolicyStatement(
                            sid="BootstrapBucketPrTemplates",
                            actions=["s3:GetObject", "s3:PutObject"],
                            resources=[f"arn:aws:s3:::{bootstrap_bucket}/{PR_ASSET_PREFIX}*"],
                        ),
                        ecr_auth,
                        ecr_push,
                    ]
                ),
            },
        )

    def _arn(self, service: str, resource: str, region: str = "us-east-1") -> str:
        return f"arn:aws:{service}:{region}:{self.account}:{resource}"

    def _pr_table_arns(self) -> list[str]:
        return [
            self._arn("dynamodb", "table/track-timing-pr-*"),
            self._arn("dynamodb", "table/track-timing-palmares-pr-*"),
            self._arn("dynamodb", "table/track-timing-pr-*/index/*"),
            self._arn("dynamodb", "table/track-timing-palmares-pr-*/index/*"),
        ]


def _github_oidc_principal(account: str, subjects: list[str]) -> iam.WebIdentityPrincipal:
    """Trust GitHub Actions OIDC tokens from this repo with one of the given subjects."""
    return iam.WebIdentityPrincipal(
        f"arn:aws:iam::{account}:oidc-provider/{OIDC_PROVIDER}",
        conditions={
            "StringEquals": {f"{OIDC_PROVIDER}:aud": "sts.amazonaws.com"},
            "StringLike": {f"{OIDC_PROVIDER}:sub": subjects},
        },
    )
