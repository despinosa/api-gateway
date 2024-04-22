FROM python:3.11-alpine

# ENV CELERY_BROKER_URL redis://redis:6379/0
ENV CELERY_RESULT_BACKEND redis://redis:6379/0
ENV CELERY_BROKER_URL amqp://guest:guest@localhost:5672/0
ENV C_FORCE_ROOT true
ENV MQ_URL amqp://guest:guest@localhost:5672/0
ENV MQ_EXCHANGE ratelimiting

COPY . /queue
WORKDIR /queue

RUN pip install -U setuptools pip
RUN pip install -r requirements.txt
