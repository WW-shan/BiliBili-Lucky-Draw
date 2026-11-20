# Public IP and Bilibili Workflow Design

## Goal

Provide authenticated HTTPS access to the Selenium management UI on the host's public IP, keep backend ports private, and make the Bilibili worker report real blockers instead of silently treating failed scans as successful runs.

## Public entry

- Preserve the existing Caddy and HAProxy listeners on ports 80 and 443.
- Add a separate Caddy gateway on port 9443 using the host's current Let's Encrypt IP certificate and a strong Basic Auth password.
- Bind MySQL, Selenium WebDriver, and noVNC host ports to loopback only. The gateway proxies to Selenium on loopback.
- Extend the existing ACME renewal hook to restart the new gateway after certificate renewal.

## Worker reliability

- Replace the tracked `test_id` placeholders with the authenticated account UID in local ignored runtime configuration.
- Fix the malformed XPath candidate list and prevent missing selectors from being passed to Selenium.
- Use a bounded browser page-load strategy and always close a created WebDriver session.
- Detect Bilibili HTTP 412 verification blocks and stop that scan with a clear error. Do not attempt to evade or automate the verification.
- Keep database credentials out of tracked source and logs; rotate the current MySQL accounts without deleting the existing database volume.
- Wait for MySQL and Selenium readiness before the scheduled worker runs.

## Verification

- Unit tests cover XPath candidates, missing-selector handling, and HTTP 412 classification.
- Deployment checks cover Compose validation, database connectivity, Selenium readiness, Basic Auth, TLS, and the public IP route.
- A read-only Bilibili scan may be verified when the site permits access. No like, follow, comment, or repost is used as a smoke test while Bilibili is returning HTTP 412.

## Known external constraint

The current host receives HTTP 412 from all configured Bilibili dynamic pages. The worker can be made reliable about reporting and pausing on that response, but a fully automatic scan cannot be claimed until Bilibili allows the authenticated browser session to load those pages.
