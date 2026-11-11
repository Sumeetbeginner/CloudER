#!/usr/bin/env sh
set -eu
# Run on the worker's Docker host. No implicit network pulls in provisioning jobs.
docker pull "${CLOUDER_COMPUTE_IMAGE:-nginxinc/nginx-unprivileged:1.28-alpine}"
docker pull "${CLOUDER_DATABASE_IMAGE:-postgres:17-alpine}"
docker pull "${CLOUDER_LOADBALANCER_IMAGE:-haproxy:3.0-alpine}"
