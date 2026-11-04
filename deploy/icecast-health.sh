#!/bin/sh
exec curl --fail --silent --max-time 3 --output /dev/null http://127.0.0.1:8000/status-json.xsl
