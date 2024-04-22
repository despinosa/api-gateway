import collections
import concurrent.futures
import functools
import json
import logging
import operator
import os
import pygtrie
import signal
import threading
import time

from typing import Any, Optional
from collections.abc import MutableMapping

import flask
import celery.states
import kombu
import kombu.mixins
import rabbitmq_pika_flask as rpf
import requests

import worker

MQ_URL = os.environ.get('MQ_URL', 'amqp://guest:guest@localhost:5672/0')
MQ_EXCHANGE = os.environ.get('MQ_EXCHANGE', 'ratelimiting')
API_HOST = os.environ.get('API_HOST', 'api.github.com')
API_PROTOCOL = os.environ.get('API_PROTOCOL', 'https')
API_BASE = f'{API_PROTOCOL}://{API_HOST}'

dev_mode: bool = True
app: flask.Flask = flask.Flask(__name__)
rabbit: rpf.RabbitMQ = rpf.RabbitMQ()

app.logger.setLevel(logging.INFO)
app.config['MQ_URL'] = MQ_URL
app.config['MQ_EXCHANGE'] = MQ_EXCHANGE
time.sleep(3)
rabbit.init_app(app, '', body_parser=json.loads)


class RateLimitExceeded(RuntimeError):
    def __init__(self, path='', ip_address='0.0.0.0'):
        self._path = path
        self._ip_address = ip_address

    def __str__(self):
        if self._ip_address == '0.0.0.0':
            return f'Too many requests from any location to path "{self._path}"'
        else:
            return (f'Too many requests from {"IP " + self._ip_address}'
                    f'to path "{self._path}"')


class ThrottleRegistry:
    _data: MutableMapping[str, set[str]] = pygtrie.StringTrie({'/': set()})

    def __init__(self):
        self._lock = threading.Lock()
    
    def set_throttled(prefix: str, ip_address: str) -> None:
        with self._lock:
            if prefix not in self._data:
                self._data[prefix] = set()
            self._data[prefix].add(ip)

    def unset_throttled(prefix: str, ip_address: str) -> None:
        with self._lock:
            if prefix not in self._data:
                self._data[prefix] = set()
            self._data[prefix].remove(ip_address)

    def is_throttled(self, prefix: str, ip_address: str) -> bool:
        with self._lock:
            return ip_address in self._data.longest_prefix(prefix) or set()

registry = ThrottleRegistry()

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def proxy(path: str) -> flask.Response:
    ip_address = (flask.request.remote_addr if flask.request.remote_addr
                  else '0.0.0.0')
    if registry.is_throttled(path, ip_address):
        raise RateLimitExceeded()
    worker.celery.send_task('tasks.hit', args=[path, ip_address])

    proxied = requests.request(  # ref. https://stackoverflow.com/a/36601467/248616
        method=flask.request.method,
        url=flask.request.url.replace(flask.request.host_url, f'{API_BASE}/'),
        headers={k: v for k, v in flask.request.headers
                 if k.lower() != 'host'}, # exclude 'host' header
        data=flask.request.get_data(),
        cookies=flask.request.cookies,
        allow_redirects=False,
    )

    # region exlcude some keys in :proxied response
    excluded_headers = ['content-encoding', 'content-length',
                        'transfer-encoding', 'connection']
    # NOTE: we here exclude all "hop-by-hop headers" defined by RFC 2616 section
    #       13.5.1 ref. https://www.rfc-editor.org/rfc/rfc2616#section-13.5.1
    headers = [(k, v) for k, v in proxied.raw.headers.items()
               if k.lower() not in excluded_headers]

    return flask.Response(proxied.content, proxied.status_code, headers)

@app.route('/_/free/<path:subpath>', methods=['PUT'])
def throttle_assignment(subpath: str) -> flask.Response:
    if flask.request.json:
        if type(flask.request.json) is not list and type(flask.request.json[0]) is int:
            global registry
            for ip_address in ip_addresses:
                registry.set_throttled(subpath, ip_address)
        else:
            raise ValueError(f"Unexpected content {flask.request.json}")
    else:
        registry.unset_throttled(subpath, 0)

@app.route('/_/free/<path:subpath>', methods=['PUT'])
def free_assignment(subpath: str) -> flask.Response:
    if flask.request.json:
        if (type(flask.request.json) is list
                and type(flask.request.json[0]) is int):
            global registry
            for ip_address in ip_addresses:
                registry.unset_throttled(subpath, ip_address)
        else:
            raise ValueError(f"Unexpected content {flask.request.json}")
    else:
        registry.unset_throttled(subpath, 0)

@app.route('/_/healthz')
def health_check() -> flask.Response:
    return flask.jsonify("OK")

@app.errorhandler(ValueError)
def handle_value_error(error):
    response = flask.jsonify({'status': 'fail',
                              'message': str(error)})
    response.status_code = 400
    return response

@app.errorhandler(RateLimitExceeded)
def handle_rate_limited(exception):
    response = flask.jsonify({'status': 'fail',
                              'message': str(exception)})
    response.status_code = 429
    return response

@rabbit.queue(routing_key='freed')
def freed_event(routing_key: str, body: dict[str, Any]) -> None:
    if 'status' in body:
        if body['status'] != 'success':
            app.logger.error('%s event with bad status %s: "%s"', routing_key,
                             body['status'], body)
    else:
        app.logger.error('Malformed %s event: %s', routing_key, body)
    if 'data' in body:
        app.logger.debug('Freeing path "%s" for IP %s',
                         body['data'].get('path', ''),
                         body['data'].get('ipAddress', ''))
        global registry
        registry.unset_throttled(body['data'].get('path', ''),
                                 body['data'].get('ipAddress', ''))
        app.logger.warning('Freed path "%s" for IP %s',
                           body['data'].get('path', ''),
                           body['data'].get('ipAddress', ''))
    else:
        app.logger.error('Malformed %s event: %s', routing_key, body)

@rabbit.queue(routing_key='throttled')
def throttled_event(routing_key: str, body: dict[str, Any]) -> None:
    if 'status' in body:
        if body['status'] != 'success':
            app.logger.error('%s event with bad status %s: "%s"', routing_key,
                             body['status'], body)
    else:
        app.logger.error('Malformed %s event: %s', routing_key, body)
    if 'data' in body:
        app.logger.debug('Throttling path "%s" for IP %s',
                         body['data'].get('path', ''),
                         body['data'].get('ipAddress', ''))
        global registry
        registry.set_throttled(body['data'].get('path', ''),
                               body['data'].get('ipAddress', ''))
        app.logger.warning('Throttled path "%s" for IP %s',
                           body['data'].get('path', ''),
                           body['data'].get('ipAddress', ''))
    else:
        app.logger.error('Malformed %s event: %s', routing_key, body)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
