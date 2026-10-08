# Pinned by digest; Dependabot (docker) proposes updates.
FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.13@sha256:0fddf7542198843bd5a488b17e35473659fdf69f0788c7b3563f0231edf3384d

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir --require-hashes -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY app/ ${LAMBDA_TASK_ROOT}/app/
COPY static/ ${LAMBDA_TASK_ROOT}/static/

ENV PYTHONUNBUFFERED=1

CMD ["app.main.handler"]
