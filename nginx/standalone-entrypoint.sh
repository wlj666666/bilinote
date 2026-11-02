#!/bin/sh
set -eu

: "${BACKEND_PORT:=8483}"
export BACKEND_PORT
envsubst '${BACKEND_PORT}' \
  < /etc/nginx/templates/standalone.conf.template \
  > /etc/nginx/conf.d/default.conf

exec nginx -g "daemon off;"
