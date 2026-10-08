# Pinned by digest; Dependabot (docker) proposes updates.
FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.14@sha256:b81a4aa3bc1d56999090333cefea611e5a84bb4e2638c1ae4107fdc9b8622da3

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir --require-hashes -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY app/ ${LAMBDA_TASK_ROOT}/app/
COPY static/ ${LAMBDA_TASK_ROOT}/static/

ENV PYTHONUNBUFFERED=1

CMD ["app.main.handler"]
