#!/bin/sh
set -eu

primary_domain="${NGINX_PRIMARY_DOMAIN:-localhost}"
cert_dir="/etc/letsencrypt/live/${primary_domain}"
template="/etc/nginx/templates/videofetch.http.conf.template"

if [ -f "${cert_dir}/fullchain.pem" ] && [ -f "${cert_dir}/privkey.pem" ]; then
    template="/etc/nginx/templates/videofetch.https.conf.template"
fi

envsubst '${NGINX_SERVER_NAME} ${NGINX_PRIMARY_DOMAIN}' \
    < "${template}" \
    > /etc/nginx/conf.d/default.conf
