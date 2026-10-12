#!/bin/sh
# Dumps the database once a day: to /backups, and to the Cloud Storage bucket
# BUCKET names.
#
# The upload signs in as the service account attached to the Compute Engine VM
# this runs on, so there is no key to keep. The dumps kept here are pruned after
# KEEP_DAYS; the bucket's own lifecycle rule prunes what is uploaded.
#
#   backup.sh         dump now, then every 24 hours
#   backup.sh once    dump now and exit, failing if the dump or upload did
set -u

# Docker makes the dumps' directory, as root, when the stack first starts. Root
# does one thing here: hand that directory to an ordinary user, then become it.
if [ "$(id -u)" = 0 ]; then
	chown postgres:postgres /backups
	exec gosu postgres "$0" "$@"
fi

KEEP_DAYS=7
BUCKET=attune-haverkos-backup
TOKEN_URL="http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"

upload() {
	token=$(curl -sf -m 10 -H "Metadata-Flavor: Google" "$TOKEN_URL" |
		sed -n 's/.*"access_token" *: *"\([^"]*\)".*/\1/p')
	if [ -z "$token" ]; then
		echo "backup: no credentials from the VM's service account; $1 was not uploaded" >&2
		return 1
	fi
	curl -sf -m 600 -o /dev/null -X POST --data-binary "@/backups/$1" \
		-H "Authorization: Bearer $token" -H "Content-Type: application/octet-stream" \
		"https://storage.googleapis.com/upload/storage/v1/b/$BUCKET/o?uploadType=media&name=$1" || {
		echo "backup: uploading $1 to $BUCKET failed" >&2
		return 1
	}
	echo "backup: uploaded $1 to gs://$BUCKET"
}

dump() {
	name="attune-$(date -u +%Y%m%dT%H%M%SZ).dump"
	# Written under another name first, so a dump cut short is never taken for a whole one.
	if ! pg_dump --format=custom --no-owner --file="/backups/$name.part" "$DATABASE_URL"; then
		echo "backup: pg_dump failed" >&2
		rm -f "/backups/$name.part"
		return 1
	fi
	mv "/backups/$name.part" "/backups/$name"
	echo "backup: wrote $name ($(du -h "/backups/$name" | cut -f1))"
	find /backups -name 'attune-*.dump' -mtime "+$KEEP_DAYS" -delete
	upload "$name"
}

if [ "${1:-}" = "once" ]; then
	dump
	exit $?
fi

while true; do
	dump
	sleep 86400
done
