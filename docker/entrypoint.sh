#!/bin/sh

# Load PUID and PGID from environment variables
PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Reject UID/GID 0 — running as root defeats the non-root setup.
if [ "$PUID" -lt 1 ] 2>/dev/null || [ "$PGID" -lt 1 ] 2>/dev/null; then
	echo "ERROR: PUID and PGID must be >= 1 (got PUID=$PUID, PGID=$PGID)" >&2
	exit 1
fi

# Modify the existing user and group to match PUID and PGID
if [ "$(id -u appuser)" != "$PUID" ] || [ "$(id -g appuser)" != "$PGID" ]; then
	sed -i "s/^appuser:x:[0-9]*:[0-9]*:/appuser:x:$PUID:$PGID:/" /etc/passwd
	sed -i "s/^appgroup:x:[0-9]*:/appgroup:x:$PGID:/" /etc/group
fi

# Ensure data directory exists with proper ownership
# (Docker creates bind-mount directories as root if they don't exist on the host)
mkdir -p /data
chown -R appuser:appgroup /data

# Switch to appuser and execute the command passed as arguments
exec su-exec appuser "$@"
