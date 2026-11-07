# Contoso Orders user guide

Contoso Orders is the order management web app used by Sales and Finance. This guide covers the
features the service desk gets the most questions about.

## Orders page

The orders page (`/`) lists every open and recent order with its customer, total and status.
Statuses are `processing`, `shipped` and `on hold`. Orders on hold need a Finance approval.

## Exporting orders to CSV

Click **Export CSV** on the orders page, or open `/export.csv` directly. The file is downloaded as
`orders.csv` with the columns `id, customer, total, status`.

The export encoding can be chosen per request with the `encoding` query parameter:

| Value | Use it for |
|-------|-----------|
| `utf-8` | Recommended. Works for every customer name. |
| `utf-8-sig` | Excel on Windows (adds a byte-order mark so accents display correctly). |
| `latin-1` | Legacy finance tools that cannot read UTF-8. |
| `ascii` | Very old importers only. Fails for names with accents. |

Example: `/export.csv?encoding=utf-8`.

The default encoding for the whole installation is set by the administrator with the
`EXPORT_ENCODING` setting.

## Health check

`/health` returns the application status and version, for example
`{"status": "ok", "version": "2.4.0"}`.
