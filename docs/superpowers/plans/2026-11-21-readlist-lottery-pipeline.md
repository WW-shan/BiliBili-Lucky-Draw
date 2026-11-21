# Readlist-Aware Lottery Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-feed scan with readlist-aware discovery, official
lottery verification, and a separate verified action queue.

**Architecture:** Collection sources are resolved through Bilibili readlist
APIs. Raw links enter status `0`; a verification service moves confirmed
official lotteries to status `5` and records them in `t_lottery_ready`; the
existing share flow consumes only status `5`.

**Tech Stack:** Python 3.10, Selenium, MySQL 5.7, unittest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-21-readlist-lottery-pipeline-design.md`

## Global Constraints

- Do not bypass Bilibili 412 or manual verification.
- Never auto-share without a non-empty official `lottery_id`.
- Never treat a collection account as the final lottery target.
- Discovery interval: 4 hours.
- Action interval: 15 minutes.
- Verification batch: 30.
- Share batch: 10.

### Task 1: Readlist selection

**Files:** `service/search_draw_dynamic_service/SearchDynamicByUps.py`,
`tests/test_search_dynamic.py`

- [ ] Write tests for selecting the newest normal/official `100680137`
  readlists, exact-name readlists, and the newest year readlist.
- [ ] Run tests and verify RED.
- [ ] Implement `SOURCE_SPECS`, readlist parsing, and selection helpers.
- [ ] Run tests and verify GREEN.

### Task 2: Readlist discovery

**Files:** `service/search_draw_dynamic_service/SearchDynamicByUps.py`,
`tests/test_search_dynamic.py`

- [ ] Write tests proving readlist discovery scans the newest article, caches
  successful articles, and never walks older article backlogs.
- [ ] Run tests and verify RED.
- [ ] Implement generic browser JSON fetching, readlist discovery, article
  selection, and insertion through the existing dedupe path.
- [ ] Run tests and verify GREEN.

### Task 3: Verified ready queue

**Files:** `dao/lottery_ready_dao.py`, `dao/draw_dynamic_dao.py`,
`tests/test_lottery_ready_dao.py`

- [ ] Write failing tests for creating the ready table, upserting a confirmed
  lottery, and querying the soonest-expiring verified candidates.
- [ ] Run tests and verify RED.
- [ ] Implement the DAO and status-5 query.
- [ ] Run tests and verify GREEN.

### Task 4: Verification service

**Files:** `service/share_service/share_one_dynamic.py`,
`service/lottery_service/verify_lottery_service.py`,
`tests/test_verify_lottery_service.py`

- [ ] Write failing tests for confirmed, explicit non-lottery, and transient
  API results.
- [ ] Run tests and verify RED.
- [ ] Refactor notice parsing into a structured checker and implement the
  verification service with cooldown handling.
- [ ] Run tests and verify GREEN.

### Task 5: Action queue and scheduling

**Files:** `service/share_service/share_from_biliLink.py`, `main.py`,
`utils/globals.py`, `docker-compose.yml`, `.env.example`,
`tests/test_share_from_biliLink.py`, `tests/test_main.py`

- [ ] Write failing tests showing sharing reads status `5`, discovery and action
  have independent schedules, and unconfirmed rows do not share.
- [ ] Run tests and verify RED.
- [ ] Implement status-5 sharing, separate scheduler jobs, and configuration.
- [ ] Run tests and verify GREEN.

### Task 6: Full verification and controlled live run

**Files:** database state and runtime configuration only.

- [ ] Run all unit tests, compileall, compose config check, and diff check.
- [ ] Archive legacy status-0 candidates as excluded.
- [ ] Run one discovery cycle and inspect readlist/source counts.
- [ ] Run one verification batch and inspect status `5`, `4`, and cooldown rows.
- [ ] Run a controlled one-share cycle and confirm a `t_share_info` row.
- [ ] Restore `SHARE_BATCH_LIMIT=10`, start the worker, and confirm it remains
  running with separate discovery/action schedules.
