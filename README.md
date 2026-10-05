# ynab-dashboard

A small self-hosted dashboard for a couple who each have their own YNAB account. It syncs both
accounts and shows charts and statistics for each person and for both of you together.

- Income vs spending per month, savings rate, how much was invested
- Where each income goes (spent / invested / left over) and each person's share of income vs spending
- Net worth over time, stacked per person
- Spending by category and group, top categories by month, who pays for what
- This month's plan: assigned vs spent, Ready to Assign, Age of Money
- Top payees, biggest purchases, account balances
- A Categories page to combine categories from both budgets (e.g. one in English, one in French)

FastAPI + SQLite + Chart.js in one container, about 40 MiB of RAM.

## Setup

1. Each person creates a Personal Access Token in YNAB: Account Settings → Developer Settings → New Token.
2. Configure:
   ```sh
   cp .env.example .env              # paste the tokens
   cp config.example.yml config.yml  # names, plans, payees between you
   ```
3. Run:
   ```sh
   docker compose up -d --build
   ```
   Open http://localhost:3003. The first sync starts right away and then repeats every 30 minutes.

There is no login: run it on a trusted network or behind something like Tailscale.

## How the numbers work

The conventions match YNAB's own reports:

- Only on-budget accounts count towards income and spending.
- Income is everything categorised to *Inflow: Ready to Assign*. Spending is every other
  categorised line, so refunds reduce spending. Transfers between on-budget accounts are ignored.
- Categories in `savings_groups` count as invested, not spending.
- Net worth includes every account, tracking accounts too.
- In the Together view, transactions with the payees/categories under `combined:` are left out,
  so money moving between the two of you isn't counted twice.

Categories with the same name (ignoring case, accents and emoji) are combined automatically.
Everything else is mapped on the Categories page. Two categories of the same budget only merge
when you map them together.

## Data

- `data/ynab.sqlite`: a mirror of the YNAB plans, kept current with YNAB's delta requests
  (2 API calls per person per sync; YNAB allows 200 per hour per token). Delete it to force a
  full re-download.
- `data/mappings.json`: the category mappings from the Categories page. Back this one up.

## Development

The code is in `app/`: `sync.py` pulls from the YNAB API, `stats.py` does the aggregation,
`main.py` is the API, and `static/` is the frontend (no build step). After changes,
run `docker compose up -d --build`.
