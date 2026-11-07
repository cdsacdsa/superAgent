"""Contoso Orders 2.4.0: a tiny demo product with a planted bug, used to show the product sandbox.

Known issue KI-2041: since 2.4.0 the CSV export defaults to ASCII, so it fails with HTTP 500 as
soon as a customer name contains a non-ASCII character. Workaround: ``/export.csv?encoding=utf-8``
(or ``EXPORT_ENCODING=utf-8``). Standard library only, so it runs in any Python image.
"""

import csv
import io
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VERSION = "2.4.0"
LOG_DIR = os.environ.get("PRODUCT_LOG_DIR", "/tmp/product")
ENCODINGS = {"ascii", "utf-8", "utf-8-sig", "latin-1"}
ORDERS = [
    {"id": 1001, "customer": "Acme Ltd", "total": 1250.00, "status": "shipped"},
    {"id": 1002, "customer": "Northwind Traders", "total": 310.50, "status": "processing"},
    {"id": 1003, "customer": "João Gonçalves", "total": 89.90, "status": "shipped"},
    {"id": 1004, "customer": "Fabrikam Inc", "total": 4020.00, "status": "on hold"},
]

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(filename=os.path.join(LOG_DIR, "app.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("contoso.orders")


def export_csv(orders: list[dict], encoding: str | None = None) -> bytes:
    # 2.4.0 regression: the default used to be utf-8 (KI-2041)
    encoding = encoding or os.environ.get("EXPORT_ENCODING", "ascii")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "customer", "total", "status"])
    for o in orders:
        writer.writerow([o["id"], o["customer"], f"{o['total']:.2f}", o["status"]])
    return buf.getvalue().encode(encoding)


def index_html() -> str:
    rows = "".join(f"<tr><td>{o['id']}</td><td>{o['customer']}</td><td>{o['total']:.2f}</td><td>{o['status']}</td></tr>"
                   for o in ORDERS)
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>Contoso Orders</title></head><body>"
            f"<h1>Orders</h1><table id='orders'><tr><th>Id</th><th>Customer</th><th>Total</th><th>Status</th></tr>"
            f"{rows}</table><p><a id='export' href='/export.csv'>Export CSV</a></p>"
            f"<footer>Contoso Orders {VERSION}</footer></body></html>")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            return self._send(200, "text/html; charset=utf-8", index_html().encode())
        if url.path == "/health":
            return self._send(200, "application/json", json.dumps({"status": "ok", "version": VERSION}).encode())
        if url.path == "/orders.json":
            return self._send(200, "application/json", json.dumps(ORDERS, ensure_ascii=False).encode())
        if url.path == "/export.csv":
            enc = (parse_qs(url.query).get("encoding") or [None])[0]
            if enc and enc not in ENCODINGS:
                return self._send(400, "text/plain", f"unsupported encoding {enc}".encode())
            try:
                body = export_csv(ORDERS, enc)
            except Exception:
                log.exception("export failed ref=ERR-EXPORT-500 encoding=%s", enc or os.environ.get("EXPORT_ENCODING", "ascii"))
                return self._send(500, "text/html; charset=utf-8",
                                  b"<h1>Internal Server Error</h1><p>Export failed (ref: ERR-EXPORT-500)</p>")
            log.info("export ok rows=%d encoding=%s", len(ORDERS), enc)
            return self._send(200, "text/csv; charset=" + (enc or "ascii"), body,
                              {"Content-Disposition": "attachment; filename=orders.csv"})
        self._send(404, "text/plain", b"not found")

    def _send(self, code: int, ctype: str, body: bytes, headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)


if __name__ == "__main__":
    log.info("Contoso Orders %s starting", VERSION)
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", "3000"))), Handler).serve_forever()
