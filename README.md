# API Gateway

A proof of concept implementation of a scalable API gateway with rate limiting capabilities. Leverages [Docker Compose](https://docs.docker.com/compose/) for orchestrating a [Flask](http://flask.pocoo.org/) API server application a [Celery](http://www.celeryproject.org/) task executor with [Redis](https://redis.io/) as its results data store and a [RabbitMQ](https://www.rabbitmq.com/) message queue for communication beween API server and task executor nodes.

### Installation

```bash
git clone https://github.com/despinosa/api-gateway
```

### Build & Launch

```bash
docker-compose up -d --build
```

This will expose the Flask application's endpoints on port `5001`.

To add more workers:
```bash
docker-compose up -d --scale worker=5 --no-recreate
```

To shut down:

```bash
docker-compose down
```


To view the minion endpoint code, see module [api/app.py](api/app.py). It also contains the callback for broadcasted messages from the master task executor node to minion API server nodes.

The code for the master task executor resides in the [celery-queue/tasks.py](celery-queue/tasks.py) module.

---

adapted from [https://github.com/mattkohl/docker-flask-celery-redis](https://github.com/mattkohl/docker-flask-celery-redis)
