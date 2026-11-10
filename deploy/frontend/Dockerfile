FROM nginx:1.30.6-alpine@sha256:583b7f2fce1cdb8da5ad5c39ebd3c33b98b87d493190e548bdee1fa8c619c311
USER root
# Targeted vendor security fix, no global apk upgrade.
RUN apk add --no-cache zlib=1.4-r0
COPY deploy/frontend/nginx.conf /etc/nginx/nginx.conf
COPY deploy/frontend/start.sh /start.sh
USER nginx
EXPOSE 8080
STOPSIGNAL SIGTERM
ENTRYPOINT ["/bin/sh", "/start.sh"]
