# Contoso Orders known issues

Known errors confirmed by the product team, with their workarounds. Check here before escalating.

## KI-2041: CSV export fails with Internal Server Error (ERR-EXPORT-500) in 2.4.0

**Affected versions:** 2.4.0. **Fixed in:** 2.4.1 (release planned).

**Symptoms:** clicking **Export CSV** shows "Internal Server Error" with reference
`ERR-EXPORT-500`. The orders page itself works. The application log shows a
`UnicodeEncodeError: 'ascii' codec can't encode character` traceback logged as `export failed`.

**Cause:** 2.4.0 changed the default export encoding from UTF-8 to ASCII. Any customer name with a
non-ASCII character (for example `ã`, `ç`, `é`, `ü`) makes the whole export fail.

**Workaround for users:** export with `/export.csv?encoding=utf-8` (or `encoding=utf-8-sig` for
Excel on Windows). A bookmark with that URL is the easiest fix for end users.

**Workaround for administrators:** set `EXPORT_ENCODING=utf-8` for the application and restart it.
This needs a standard change on production.

## KI-1987: Orders on hold are not included in the weekly e-mail digest

**Affected versions:** 2.3.0 to 2.4.0. **Workaround:** filter the orders page by status `on hold`.
