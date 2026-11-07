# Contoso Orders troubleshooting runbook

Steps for L2/L3 engineers. Reproduce in the product sandbox before touching production.

## Where to look

- Application log: `/tmp/product/app.log` in the sandbox (`/var/log/contoso/app.log` on servers).
  Errors carry a reference such as `ref=ERR-EXPORT-500` that matches the code shown to users.
- Health and version: `GET /health`.
- Raw data: `GET /orders.json` returns the orders as JSON.

## Reproducing a user-reported error

1. Check the version with `curl -s http://localhost:3000/health`.
2. Repeat the user's action, for example
   `curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/export.csv`,
   or drive the UI with `browse http://localhost:3000/ --step click:#export`.
3. Read the end of the application log: `tail -n 50 /tmp/product/app.log`.
4. Match the error reference and the traceback against the known issues page.
5. Verify the workaround in the sandbox before you recommend it to the user.

## Escalating to the product team

If no known issue matches, hand over to **L3 Support** with: version, exact steps, the error
reference, the log excerpt and what you already ruled out.
