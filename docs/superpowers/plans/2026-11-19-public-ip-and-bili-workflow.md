# Public IP and Bilibili Workflow Implementation Plan

> **For agentic workers:** Execute inline in this session. Keep the existing running Compose project and preserve the `db_data` volume.

**Goal:** Securely publish the Selenium dashboard on the host IP and make the scheduled worker stable and honest about Bilibili access failures.

**Architecture:** Preserve the existing host Caddy/HAProxy on 80/443. Add an authenticated Caddy gateway on 9443 using the renewed IP certificate; keep MySQL, Selenium, and noVNC host ports on loopback. Repair worker identity and selector handling, then verify all local components and stop at a Bilibili verification block rather than bypassing it.

**Tech Stack:** Docker Compose, Caddy 2.11.9, HAProxy 3.2, MySQL 5.7, Selenium Grid 4.52, Python 3.10, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-19-public-ip-and-bili-workflow.md`

## Global Constraints

- Public URL uses `https://198.2.231.202:9443/ui/`; Basic Auth is required.
- Do not change the existing host services on ports 80/443 or the database volume contents.
- Never publish MySQL, raw WebDriver, or noVNC directly to the public interface.
- Never log or commit SESSDATA, MySQL passwords, notification keys, or the Basic Auth plaintext password.
- Do not bypass Bilibili HTTP 412 verification or perform real engagement actions as a smoke test.

---

### Task 1: Replace brittle dynamic-page XPath scanning

**Files:**
- Create: `tests/test_search_dynamic.py`
- Modify: `service/search_draw_dynamic_service/SearchDynamicByUps.py`

**Interfaces:**
- The scanner reads current dynamic cards with independent CSS selectors, caches detail scans, and caps each UP to two new detail pages per cycle.
- HTTP 412 and known verification pages raise a typed error that stops the cycle.

- [x] **Step 1: Write failing tests** for independent selectors, safe empty-card handling, URL normalization, and HTTP 412 classification.
- [x] **Step 2: Run the tests red** against the legacy scanner.
- [x] **Step 3: Port the fork's dynamic-card and detail-link extraction approach** and propagate verification blocks.
- [x] **Step 4: Run the regression suite** and confirm the cases pass.

### Task 2: Make worker startup and browser cleanup reliable

**Files:**
- Modify: `main.py`
- Modify: `utils/webdriver_util.py`
- Modify: `service/search_draw_dynamic_service/SearchDynamicByUps.py`
- Modify: `service/share_service/share_from_biliLink.py`

**Interfaces:**
- `init_webdriver()` still returns `(driver, chains)`.
- Search and share methods raise or log a typed access-block error distinctly from selector failures.

- [x] **Step 1: Add tests** for verified Cookie login and preventing the share stage after a scan failure.
- [x] **Step 2: Run those tests red** against optimistic login and the old worker flow.
- [x] **Step 3: Use Compose health checks, nonblocking page navigation with explicit waits, bounded page-load timeouts, safe driver cleanup, and pause scheduling after auth or verification blocks.**
- [x] **Step 4: Run `python3 -m unittest discover -s tests -v`** and confirm worker guard behavior.

### Task 3: Set the worker identity and retire tracked credentials

**Files:**
- Modify locally: `.env`
- Modify: `.gitignore`
- Modify: `.dockerignore`
- Modify: `docker-compose.yml`
- Modify: `dao/init_db.py`
- Create: `.env.example`

**Interfaces:**
- Compose reads local secrets from `.env`; the worker's SESSDATA and private overrides remain in `.env.runtime`.
- The authenticated UID is read from the already-verified `/x/web-interface/nav` response and used for `my_user_id` and `multi_users`.

- [x] **Step 1: Add `.env` to ignore rules and remove it from the Git index without deleting the local file.**
- [x] **Step 2: Replace hard-coded MySQL values in Compose** with required environment substitutions and add non-secret examples.
- [x] **Step 3: Rotate the MySQL application/root passwords in the existing volume**, update only local `.env`, and set mode `600`.
- [x] **Step 4: Remove password-bearing DB config logging** and pass both whitespace checks.

### Task 4: Publish the protected gateway on the IP

**Files:**
- Create: `deploy/Caddyfile`
- Create locally: `.gateway.env` and `.gateway.credentials`
- Modify: `docker-compose.yml`
- Modify: `.gitignore`
- Modify: `.dockerignore`
- Update host ACME reload hook for the IP certificate.

**Interfaces:**
- Gateway listens on host-network port `9443` and proxies to `127.0.0.1:5555`.
- Caddy receives only the hashed Basic Auth password; the plaintext is held in a local mode-`600` file and shown to the user once.

- [x] **Step 1: Generate a random admin password and a Caddy bcrypt hash** without printing the plaintext to command output.
- [x] **Step 2: Write and validate the standalone Caddyfile** with the current IP certificate.
- [x] **Step 3: Bind Compose ports 3206, 5555, and 7900 to `127.0.0.1`**, add the host-network Caddy gateway on 9443, and retain `restart: unless-stopped`.
- [x] **Step 4: Extend the ACME reload hook** so the gateway restarts after renewal, then start the gateway.
- [x] **Step 5: Verify** unauthenticated `401`, authenticated `/ui/` `200`, valid IP TLS, backend loopback bindings, and existing 80/443 listeners.

### Task 5: Rebuild and verify the running service

**Files:**
- Runtime only: `db_data/`, `.env.runtime`, `.gateway.env`, `.gateway.credentials`

**Interfaces:**
- Existing scheduled main container remains the worker entry point.
- Bilibili scan errors surface as explicit 412 verification blockers rather than invalid-argument Selenium errors.

- [x] **Step 1: Rebuild the worker image** with the pinned Python 3.10 base and public PyPI source.
- [x] **Step 2: Recreate affected Compose services** and verify MySQL/Selenium health and worker-to-MySQL connectivity.
- [x] **Step 3: Verify IP access and confirm the worker reports Bilibili HTTP 412 without reaching the share stage.**
- [x] **Step 4: Report active services, resource usage, dashboard URL, credentials location, and the external verification blocker.**
