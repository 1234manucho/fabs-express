import os
bind='0.0.0.0:'+os.getenv('PORT','10000')
workers=int(os.getenv('WEB_CONCURRENCY','2'))
threads=4
worker_class='gthread'
timeout=120
graceful_timeout=30
keepalive=5
max_requests=1000
max_requests_jitter=100
# Provider callback URLs contain capability secrets. Do not log raw request paths.
accesslog=None
errorlog='-'
loglevel='info'
capture_output=True
