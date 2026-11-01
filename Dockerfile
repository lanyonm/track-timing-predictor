# Pinned by digest; Dependabot (docker) proposes updates.
FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.13@sha256:cf3e082b6c28721944dfef2c252c2e6df2f65aab3c2e2a64ce1f86f80c7aa13d

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir --require-hashes -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY app/ ${LAMBDA_TASK_ROOT}/app/
COPY static/ ${LAMBDA_TASK_ROOT}/static/

ENV PYTHONUNBUFFERED=1

CMD ["app.main.handler"]
