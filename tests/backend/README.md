# Backend test suite (branch backend)

Tests are maintained in the `backend` branch and must not be merged into `main`.
No command here modifies Git branches. Production promotion is documented in the
root backend README; keep `tests/` and test Compose files outside that promotion.

Run the isolated HTTP integration suite from the repository root:

```sh
./tests/backend/run.sh
```

Requires Docker + Docker Compose with access to image/package registries. The
script starts a separate Compose project `deeplecture-test`, MySQL database
`deeplecture_test`, real PHP-FPM/Nginx, and the production FastAPI search API
with an explicitly injected deterministic encoder from `support/search.py`.
It uses port 18080 for HTTP and 13306 for MySQL. The trap removes only this
project and its disposable volumes after completion; it does not touch the
normal development stack. Do not store your work in these test volumes.

`fixtures/schema.sql` is a reference test schema, **not** the database owner's
production migration system. No DDL runs in application startup. Schema changes
must first be agreed with the database owner and reflected in the contract.

The launcher runs PHP unit assertions, Python unit/provider tests, real MySQL
worker/concurrency tests and HTTP integration in sequence. CI is defined in
`.github/workflows/backend-tests.yml` and triggers only for backend (or manually).
Use `SKIP_BUILD=1 sh tests/backend/run.sh` to reuse previously built test images;
after source changes rebuild them first. Restricted cloud proxies may require
explicit Docker build proxy arguments and a trusted CA build secret `proxy_ca`;
never disable certificate or package-signature verification.

The HTTP suite generates actual MP4/WebM samples with FFmpeg and verifies:

- upload and queued records; catalog, card and field validation;
- Nginx byte ranges and HEAD on uploaded media;
- transcript and chapter timing; PHP → real FastAPI → MySQL vector retrieval;
- incompatible vector revisions and isolation between lectures;
- partial failure (summary failed while search remains available);
- atomic retry, cleanup, preserved job history and concurrent retry requests;
- error envelopes and absence of sensitive implementation details.

Deterministic vectors verify data flow, not semantic relevance. These tests do
not replace a real-model smoke run or provider/quality evaluation.

For an already running isolated stack, the same suite can run natively:

```sh
python -m pip install -r tests/backend/requirements.txt
RUN_HTTP_TESTS=1 TEST_API_URL=http://127.0.0.1:18080 \
 DB_HOST=127.0.0.1 DB_PORT=13306 DB_NAME=deeplecture_test \
 DB_USER=deeplecture DB_PASSWORD=fixture-only-password \
 EMBEDDING_REVISION=test-fixture-v1 python -m pytest -q tests/backend/http
```

Only use a disposable `_test` database: the suite deletes fixture table data
between tests and rejects a database name without this suffix. Selecting the
HTTP suite without `RUN_HTTP_TESTS=1` explicitly reports it as skipped.
The standalone Python/PHP unit and worker suites live in their respective
subdirectories and have separate documented commands in the backend README.
