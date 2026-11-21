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
- `492426375` (糯米是个背包): newest four-digit year readlist. It is an index,
  never a final lottery target.
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
   Confirmed links move to status `5` and are recorded in `t_lottery_ready`.
   Explicit non-lotteries move to status `4`; transient API failures use a
   cooldown and leave the candidate at status `0`.
5. The action pass shares at most ten verified activities per run, preserving
   the existing 20-30 second pacing and rechecking the official notice before
   any social action.

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

- Only official `lottery_id` notices are auto-actionable.
- Charging lotteries, articles, source-account posts, expired posts, and raw
  `MAJOR_TYPE_DRAW` cards without a notice are rejected.
- A 412 or authentication failure pauses further automatic work until restart.
- Source-aware scan modes ensure collection accounts never become their own
  final action target.
