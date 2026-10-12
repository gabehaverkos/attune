# pg_dump has to be at least as new as the server it dumps: keep this on the
# stack's Postgres major version.
FROM postgres:17-alpine

RUN apk add --no-cache curl

# Where the dumps go. The stack mounts ./backups here; without a mount they stay
# in the container.
RUN mkdir /backups && chown postgres:postgres /backups

COPY backup.sh /usr/local/bin/backup.sh
RUN chmod +x /usr/local/bin/backup.sh

ENTRYPOINT ["backup.sh"]
