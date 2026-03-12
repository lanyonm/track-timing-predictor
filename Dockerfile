FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.11

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY app/ ${LAMBDA_TASK_ROOT}/app/
COPY static/ ${LAMBDA_TASK_ROOT}/static/

ENV PYTHONUNBUFFERED=1

CMD ["app.main.handler"]
