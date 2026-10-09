# Pinned by digest; Dependabot (docker) proposes updates.
FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.13@sha256:e29b87b66ee86899ac3f1e7ae19f7d761ac4504d58432810f917531f9b92687f

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir --require-hashes -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY app/ ${LAMBDA_TASK_ROOT}/app/
COPY static/ ${LAMBDA_TASK_ROOT}/static/

ENV PYTHONUNBUFFERED=1

CMD ["app.main.handler"]
