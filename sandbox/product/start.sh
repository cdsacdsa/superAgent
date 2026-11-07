#!/bin/sh
# Start the product under test in the background, then the exec API (port 8080) in the foreground.
mkdir -p /tmp/product
sh -c "$PRODUCT_START_CMD" >> /tmp/product/stdout.log 2>&1 &
exec python /opt/runner.py
