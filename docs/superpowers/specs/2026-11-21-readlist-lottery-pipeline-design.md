# Readlist-Aware Lottery Pipeline

## Goal

Discover currently active Bilibili giveaway links from verified collection
sources, verify each link against Bilibili's official interaction-lottery API,
and execute only verified activities at a sustainable rate.

## Confirmed Sources

- `100680137` (你的抽奖工具人): readlists named `抽奖合集...` and
  `官方抽奖合集...`; select the newest readlist in each family.
- `226257459` (_大锦鲤_): `抽奖专栏`.
- `126038161` (J君名): `抽奖`.
- `280604312` (抽奖工具人转发池): space forward feed; use the forwarded
  `orig_id`, never the account's own dynamic.

## Pipeline

1. Discovery runs every four hours.
2. For each readlist source, call `/x/article/up/lists`, select current
   readlists, then call `/x/article/list/web/articles` and scan only the newest
   configured article from each selected readlist.
3. Article scans are cached by article URL. Extracted links are deduplicated in
   `t_draw_dynamic` with status `0` (discovered).
4. A verification pass checks official
   `/lottery_svr/v1/lottery_svr/lottery_notice?business_type=1&business_id=...`.
   Confirmed official links move to status `5` and are recorded in
   `t_lottery_ready` with the account's current follow/repost state. If no
   official notice exists, a complete explicit text rule can still be queued.
   Explicit non-lotteries move to status `4`; transient API failures use a
   cooldown and leave the candidate at status `0`.
5. The action pass executes only the actions stored in the verified rule,
   then opens the official lottery H5 page and requires `已成功参与` before
   marking the activity complete. It preserves pacing and never defaults a
   missing rule to follow/like/comment/forward.

## Status Codes

- `0`: discovered, not yet verified
- `1`: completed share
- `2`: expired
- `3`: terminal failure
- `4`: excluded / not a confirmed lottery
- `5`: verified official lottery, ready for action

## Scheduling

- Discovery: every 4 hours.
- Action/verification: every 15 minutes.
- Verification batch: up to 30 candidates.
- Share batch: up to 10 verified activities.
- Scheduler never runs two cycles concurrently.

## Safety

- Official `lottery_id` notices are preferred and are verified again through
  the H5 participation state after actions.
- Complete explicit text rules are allowed only when the text names the
  required engagement actions; missing actions fail closed.
- Charging lotteries, articles, source-account posts, expired posts, and
  incomplete ambiguous cards are rejected or deferred.
- A 412 or authentication failure triggers a timed cooldown and automatic
  recovery with a fresh session.
- Source-aware scan modes ensure collection accounts never become their own
  final action target.
