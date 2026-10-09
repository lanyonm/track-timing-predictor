from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_dynamodb as dynamodb,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_sns as sns,
)
from constructs import Construct


class TrackTimingStack(Stack):
    """Per-environment stack: DynamoDB, Lambda (Docker), CloudWatch logs.

    Prod adds CloudFront with OAC, ACM certificate, and error alerting.
    """

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
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=is_prod,
            ),
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
        )

        # DynamoDB — palmares table (pk + sk design for per-racer queries)
        palmares_table = dynamodb.Table(
            self,
            "Palmares",
            table_name=f"track-timing-palmares-{env_name}",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=is_prod,
            ),
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
        )

        # Lambda — Docker image from ECR
        log_group = logs.LogGroup(
            self,
            "HandlerLogs",
            log_group_name=f"/aws/lambda/track-timing-{env_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        fn = lambda_.DockerImageFunction(
            self,
            "Handler",
            function_name=f"track-timing-{env_name}",
            code=lambda_.DockerImageCode.from_ecr(repository=repo, tag_or_digest=image_tag),
            memory_size=512,
            timeout=Duration.seconds(60),
            environment={
                "DYNAMODB_TABLE": f"track-timing-{env_name}",
                "PALMARES_TABLE": f"track-timing-palmares-{env_name}",
                # PR envs serve from their public Function URL, so share links use the request host
                **({"PUBLIC_BASE_URL": "https://ttp.lanyonm.org"} if is_prod else {}),
            },
            log_group=log_group,
        )

        # Grant the Lambda function read/write access to DynamoDB tables
        table.grant_read_write_data(fn)
        palmares_table.grant_read_write_data(fn)

        # Function URL — IAM auth; accessed via CloudFront OAC in prod,
        # directly (no auth required) in PR environments
        fn_url = fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.AWS_IAM if is_prod else lambda_.FunctionUrlAuthType.NONE,
        )

        CfnOutput(self, "FunctionUrl", value=fn_url.url)

        # CloudFront + custom domain (prod only)
        if is_prod:
            certificate = acm.Certificate(
                self,
                "Certificate",
                domain_name="ttp.lanyonm.org",
                validation=acm.CertificateValidation.from_dns(),
            )

            # One origin (and OAC) shared by both behaviors
            origin = origins.FunctionUrlOrigin.with_origin_access_control(fn_url)
            distribution = cloudfront.Distribution(
                self,
                "Distribution",
                default_behavior=cloudfront.BehaviorOptions(
                    origin=origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                ),
                # static/ files are requested as /static/<file>?v=<content hash> (app.main.static_url),
                # so a rebuilt file gets a new cache key. The app sends a one-year Cache-Control for
                # versioned requests, which this policy honours (default 1 day, max 1 year).
                additional_behaviors={
                    "/static/*": cloudfront.BehaviorOptions(
                        origin=origin,
                        viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                        cache_policy=cloudfront.CachePolicy(
                            self,
                            "StaticCachePolicy",
                            cache_policy_name=f"track-timing-{env_name}-static",
                            comment="static/ assets, keyed on the ?v= content hash",
                            default_ttl=Duration.days(1),
                            min_ttl=Duration.seconds(0),
                            max_ttl=Duration.days(365),
                            query_string_behavior=cloudfront.CacheQueryStringBehavior.allow_list("v"),
                            header_behavior=cloudfront.CacheHeaderBehavior.none(),
                            cookie_behavior=cloudfront.CacheCookieBehavior.none(),
                            enable_accept_encoding_gzip=True,
                            enable_accept_encoding_brotli=True,
                        ),
                    ),
                },
                domain_names=["ttp.lanyonm.org"],
                certificate=certificate,
            )

            # Workaround for CDK issue #35872: AWS requires both
            # lambda:InvokeFunctionUrl (added by with_origin_access_control)
            # and lambda:InvokeFunction for CloudFront OAC dual auth.
            # Dual auth enforcement begins November 2026; remove once CDK fixes upstream.
            fn.add_permission(
                "CloudFrontInvokeFunction",
                principal=iam.ServicePrincipal("cloudfront.amazonaws.com"),
                action="lambda:InvokeFunction",
                source_arn=f"arn:aws:cloudfront::{self.account}:distribution/{distribution.distribution_id}",
            )

            CfnOutput(self, "DistributionDomain", value=distribution.distribution_domain_name)

            # CloudWatch alarm — notify on Lambda errors
            alarm_topic = sns.Topic(self, "AlarmTopic", topic_name="track-timing-prod-alarms")
            CfnOutput(self, "AlarmTopicArn", value=alarm_topic.topic_arn)

            error_alarm = fn.metric_errors(
                period=Duration.minutes(5),
                statistic="Sum",
            ).create_alarm(
                self,
                "ErrorAlarm",
                alarm_name="track-timing-prod-errors",
                threshold=5,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            error_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))
