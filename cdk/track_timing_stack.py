from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_ecr as ecr,
    aws_lambda as lambda_,
    aws_logs as logs,
)
from constructs import Construct


class TrackTimingStack(Stack):
    """Per-environment stack: DynamoDB table + Lambda Function URL."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        repo: ecr.Repository,
        image_tag: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        is_prod = env_name == "prod"

        # DynamoDB — on-demand billing; RETAIN prod data, DESTROY ephemeral PR data
        table = dynamodb.Table(
            self,
            "Durations",
            table_name=f"track-timing-{env_name}",
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
        )

        # Lambda — Docker image from ECR
        fn = lambda_.DockerImageFunction(
            self,
            "Handler",
            function_name=f"track-timing-{env_name}",
            code=lambda_.DockerImageCode.from_ecr(
                repository=repo, tag_or_digest=image_tag
            ),
            memory_size=512,
            timeout=Duration.seconds(60),
            environment={
                "DYNAMODB_TABLE": f"track-timing-{env_name}",
                "PYTHONUNBUFFERED": "1",
            },
            log_retention=logs.RetentionDays.ONE_MONTH,
        )

        # Grant the Lambda function read/write access to the DynamoDB table
        table.grant_read_write_data(fn)

        # Function URL — public access (no IAM auth)
        fn_url = fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
        )

        CfnOutput(self, "FunctionUrl", value=fn_url.url)
