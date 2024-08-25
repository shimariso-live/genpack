#!/bin/sh
set -e
if [ ! -f /etc/hostname ]; then
	echo -n "$ARTIFACT" > /etc/hostname
fi
