# Participation Pipeline Repair Implementation Plan

> **For agentic workers:** Execute inline in this session. Preserve the existing uncommitted deployment changes and database volume.

**Goal:** Make the Bilibili giveaway scan/action chain use complete sessions and reliably wait for pages before proceeding.

**Architecture:** Scan public UP pages without injecting an incomplete logged-in session, restore all configured cookies only for account actions, and stop before writes if the `bili_jct` CSRF cookie is missing. Keep HTTP 412 handling intact, restrict the queue to giveaway cards, and use separate waits for document readiness and newly opened tabs.

**Tech Stack:** Python 3.10, Selenium 4, `unittest`, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-19-public-ip-and-bili-workflow.md`

## Global Constraints

- Do not bypass Bilibili HTTP 412 verification or automate solving a challenge.
- Do not perform real likes, follows, comments, or reposts as a smoke test.
- Preserve local secrets, the existing database volume, and unrelated uncommitted changes.
- Use unit tests with fake browser/DAO objects for regression verification.

---

### Task 1: Restrict discovered entries to giveaway cards

**Files:**
- Modify: `service/search_draw_dynamic_service/SearchDynamicByUps.py`
- Modify: `tests/test_search_dynamic.py`

**Interfaces:**
- `extract_detail_entries_from_cards(cards)` remains the source of card-level giveaway candidates.
- `search_dynamic_links_by_up(browser, base_url, note)` sends candidate dynamic URLs plus links found inside scanned giveaway detail pages to the existing DB insertion method.

- [x] **Step 1: Add a failing test** that mocks a profile feed containing a non-giveaway dynamic URL and a giveaway-card URL, then asserts only the giveaway URL reaches `dynLinks_to_db`.
- [x] **Step 2: Run that single test** with `python3 -m unittest tests.test_search_dynamic.SearchDynamicRegressionTests.test_feed_enqueues_only_giveaway_cards -v`; confirm it fails because the current scanner extracts every feed link.
- [x] **Step 3: Build the initial link list from giveaway-card entries**, while retaining nested links found in the bounded detail-page scan.
- [x] **Step 4: Re-run the focused test** and confirm only the giveaway candidate is enqueued.

### Task 2: Correct browser navigation waits

**Files:**
- Modify: `utils/webdriver_util.py`
- Modify: `service/share_service/share_one_dynamic.py`
- Modify: `service/remove_share.py`
- Modify: `tests/test_webdriver_util.py`
- Create: `tests/test_share_one_dynamic.py`

**Interfaces:**
- `ElementUtil.wait_for_page_load(browser, timeout=10)` waits until `document.readyState` is `interactive` or `complete`.
- `ElementUtil.wait_to_go(browser, previous_handles, timeout=10)` waits for a new tab relative to the handles captured before the click.

- [x] **Step 1: Add failing tests** for page readiness and for new-tab waiting with an explicit pre-click handle list.
- [x] **Step 2: Run the focused tests** with `python3 -m unittest tests.test_webdriver_util -v`; confirm they fail against the current helper.
- [x] **Step 3: Implement the two waits** and update callers: normal `get`/`refresh` paths use page readiness; only the author-profile click uses the new-tab wait with captured handles; the old-version switch relies on its subsequent element wait.
- [x] **Step 4: Run focused tests** and confirm the wait helpers pass.

### Task 3: Port the fork's unauthenticated scan and complete action-cookie flow

**Files:**
- Modify: `service/search_draw_dynamic_service/SearchDynamicByUps.py`
- Modify: `service/login_service/login_service.py`
- Modify: `service/share_service/share_from_biliLink.py`
- Modify: `.env.runtime.example`
- Modify: `tests/test_search_dynamic.py`
- Modify: `tests/test_login_service.py`
- Create: `tests/test_share_from_biliLink.py`

**Interfaces:**
- Public dynamic scanning does not call `LoginService.login_by_cookie()`.
- `cookie_value` accepts the existing raw SESSDATA value or a full Cookie header; write actions require a nonempty `bili_jct` cookie.

- [x] **Step 1: Add failing tests** for scan without login injection, full Cookie header restoration, and stopping writes when `bili_jct` is absent.
- [x] **Step 2: Run focused tests red** and confirm each missing behavior fails independently.
- [x] **Step 3: Port the fork's scan/auth separation**, bootstrap the Bilibili site before restoring cookies, and gate comment/repost actions on `bili_jct`.
- [x] **Step 4: Run focused tests green** for the scan, cookie, and write guard paths.

### Task 4: Verify the repair and runtime boundary

**Files:**
- Runtime only: worker container and existing MySQL volume.

- [x] **Step 1: Run** `python3 -m unittest discover -s tests -v` and `python3 -m compileall -q main.py service dao utils tests`.
- [x] **Step 2: Rebuild/restart the worker** only after local verification, preserving all volumes and credentials.
- [x] **Step 3: Inspect fresh worker logs and DB counts**; report the scan/participation outcome accurately and stop if Bilibili still returns HTTP 412.

**Runtime result (2026-11-20 UTC):** The first worker run started at 12:16:01 and received HTTP 412 for a public dynamic page at 12:20:31. After configuring a full Cookie with both SESSDATA and `bili_jct`, the worker was recreated at 12:55:17 and again received HTTP 412 at 12:56:34 while scanning the four subscribed UPs. This confirms the current blocker is the public-page verification response, not a missing action cookie. Counts remain zero for `t_draw_dynamic`, `t_share_info`, and `t_shared_urls`; no giveaway was discovered and no participation action ran. Stop on HTTP 412 and do not bypass the challenge.

### Final runtime verification (supersedes the earlier 412 result)

Verified on 2026-11-20 after switching scanning to the authenticated browser's space-feed API and limiting candidates to recent giveaway cards:

- Three real scan runs inserted 125 unique candidate dynamics (57, 48, and 20 respectively). Old non-giveaway rows were removed from the pending queue without deleting the database volume.
- The production scheduler processed a real batch of 5 pending dynamics from 20:42 to 20:49 UTC. All 5 completed follow/like/comment/repost and were recorded with `share_status=0`; the batch summary was `成功率 100.00%`.
- `t_share_info` contains 9 successful real participation rows and `t_followdups` contains 9 matching account-UP relations.
- The follow fallback was verified against a previously un-followed UP (`697654195`): the relation API returned `code=0`, and a subsequent relation query confirmed `followed=true`.
- The final queue state was 116 pending and 213 completed, with no failed rows. No HTTP 412 occurred in the successful runs.
- The worker was idle after the batch, with the next automatic run scheduled 3 hours later. At idle it used about 9 MiB RAM; Selenium used about 46 MiB and was healthy.
