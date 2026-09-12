# Listing maintenance

A small scheduled job that keeps a fixed-price marketplace inventory fresh.

Once a day it:

1. Reports anything needing a human — offers awaiting a response, unanswered
   buyer questions, orders to ship, and any listing that ended unexpectedly.
2. Refreshes the oldest listings: ends and relists them so each gets a new
   item id, then restores price, store category and item specifics.

Step 2 matters because a relist restores a listing exactly as it ended — old
price, old best-offer thresholds, store category reset, and only the item
specifics it had at the time. All of that has to be reapplied.

## Configuration

Set three repository secrets under **Settings → Secrets and variables → Actions**:

| secret | where it comes from |
|---|---|
| `EBAY_CLIENT_ID` | your API keyset |
| `EBAY_CLIENT_SECRET` | your API keyset |
| `EBAY_REFRESH_TOKEN` | the OAuth consent flow |

Nothing else is configurable in code. Adjust `BATCH` and the price tiers in
`refresh.py` to taste.

## Notes

Scheduled workflows are best-effort and can be delayed under load. The job
writes `heartbeat.txt` on every run so the repository never goes 60 days
without activity, which is when GitHub disables scheduled workflows.
