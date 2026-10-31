"""Gunicorn configuration for Electro."""

import os
from config import Config

# Bind to configured host and port (default 0.0.0.0:5050 for LAN access)
bind = f"{Config.ELECTRO_HOST}:{Config.ELECTRO_PORT}"

# Worker processes and concurrency model
# Single worker with multi-threading ensures single background poller ownership
workers = 1
threads = 4
timeout = 30
keepalive = 5

# Logging
loglevel = Config.ELECTRO_LOG_LEVEL.lower()
