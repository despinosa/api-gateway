FROM python:3.11-alpine

# ENV CELERY_BROKER_URL redis://redis:6379/0
ENV CELERY_RESULT_BACKEND redis://redis:6379/0
ENV CELERY_BROKER_URL amqp://guest:guest@rabbitmq:5672/0
ENV MQ_URL amqp://guest:guest@rabbitmq:5672/0
ENV MQ_EXCHANGE ratelimiting

ENV C_FORCE_ROOT true

ENV HOST 0.0.0.0
ENV PORT 5001
ENV DEBUG true

COPY . /api
WORKDIR /api

RUN pip install -U setuptools pip
RUN pip install -r requirements.txt

EXPOSE 5001

RUN pip install gunicorn

CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "3", "app:app"]
