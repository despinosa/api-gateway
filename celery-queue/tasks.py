import copy
import functools
import json
import os
import datetime

from collections.abc import MutableMapping
from typing import Dict, Tuple

import celery
import redis
import redis.lock
import pika
import pygtrie


# CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379'),
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'amqp://guest:guest@localhost:5672/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379')
MQ_URL = os.environ.get('MQ_URL', 'amqp://guest:guest@localhost:5672/0')
MQ_EXCHANGE = os.environ.get('MQ_EXCHANGE', 'ratelimiting')

scheduler = celery.Celery('tasks', broker=CELERY_BROKER_URL,
                         backend=CELERY_RESULT_BACKEND)

def mutex(function=None, key='', timeout=None):
    @functools.wraps(function)
    def decorator(function_runner):
        @functools.wraps(function_runner)
        def worker(*args, **kwargs):
            return_value = None
            # lock = celery.backend.redis.client.lock(key, timeout=timeout)
            lock = redis.lock.Lock(scheduler.backend.client, key, timeout=timeout)
            with lock:
                return_value = function_runner(*args, **kwargs)
            return return_value
        return worker
    return decorator if function is None else decorator(function)


class RateLimiterRegistry:
    blocklist: dict[str, set[int]] = {}
    _state: MutableMapping[str, dict[int, int]] = pygtrie.StringTrie()
    _default: MutableMapping[str, dict[int, int]] = pygtrie.StringTrie(
        {'/': {'152.152.152.152': 1000},
         '/categories/': {0: 1000},
         '/items/': {'152.152.152.152': 10}}
    )

    def __init__(self):
        self.reset()

    @mutex(timeout=3)
    def evaluate(self, path: str, ip_address: int, new_hits=1) -> bool:
        substate = self._state.longest_prefix(path).value or self._state['/']
        lookup = ip_address if ip_address in substate else 0
        if lookup not in substate:
            return False
        substate[lookup] -= new_hits
        if substate[lookup] <= 0:
            if path not in self.blocklist:
                self.blocklist[path] = set()
            self.blocklist[path].add(ip_address)
        return substate[lookup] <= 0
    
    @mutex(timeout=60)
    def reset(self) -> None:
        self._state = copy.deepcopy(self._default)
        self.blocklist = {}


registry = RateLimiterRegistry()

@scheduler.task(name='tasks.hit')
def hit(path: str, ip_address: str) -> Dict[str, int | str]:
    if registry.evaluate(path, ip_address):
        with pika.BlockingConnection(
                parameters=pika.URLParameters(MQ_URL)) as connection:
            with connection.channel() as channel:
                channel.basic_publish(
                    MQ_EXCHANGE, 'throttled',
                    json.dumps({'data': {'path': path,
                                         'ipAddress': ip_address}})
                )
    return {'timestamp': int(datetime.datetime.now().timestamp()),
            'path': path,
            'ip_address': ip_address}

@scheduler.task(name='tasks.cycle', ignore_result=True)
def cycle() -> None:
    newly_allowlisted = copy.deepcopy(registry.blocklist)
    registry.reset()
    for path, ip_addresses in newly_allowlisted:
        for ip_address in ip_addresses:
            with pika.BlockingConnection(
                    parameters=pika.URLParameters(MQ_URL)) as connection:
                with connection.channel() as channel:
                    channel.basic_publish(
                        MQ_EXCHANGE, 'freed',
                        json.dumps({'data': {'path': path,
                                             'ipAddress': ip_address}})
                    )

@scheduler.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    sender.add_periodic_task(60.0, cycle.s())

