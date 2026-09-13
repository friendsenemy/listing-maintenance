"""Daily listing maintenance.

Reports what needs a human, then refreshes the oldest listings.
"""
import datetime
import json
import re
import sys
import time

import ebay_client as eb

BATCH = 12          # listings refreshed per run
WATCH_SKIP = 3      # ending a listing loses its watchers; leave watched ones alone
FLOOR = 89.00 + 25.00   # list-price floor; buyer sees $89 after the sale

# LIST prices. A store-wide "$25 off" sale event (rule-based, auto-includes
# new item ids) takes every one of these down to the real selling tier, so
# the buyer pays $99.47 / $119.47 / $169.47 and sees a strikethrough. If the
# sale event ever lapses, these are $25 too high — renew it, don't edit these.
MARKDOWN = 25.00
PRICE = {
    "biker": 99.47 + MARKDOWN,
    "thermo": 169.47 + MARKDOWN,
    "nascar": 119.47 + MARKDOWN,
    "gasoil": 119.47 + MARKDOWN,
    "pinup": 119.47 + MARKDOWN,
    "soda": 119.47 + MARKDOWN,
    "other": 119.47 + MARKDOWN,
}

STORE_CATEGORY = {
    "biker": "24502016015",
    "nascar": "24502017015",
    "gasoil": "24502018015",
    "pinup": "24502019015",
    "thermo": "24502020015",
    "soda": "24502021015",
    "other": "24502022015",
}

USAGE = {
    "biker": ["Motorcycle Shop", "Biker Bar", "Garage", "Man Cave", "Repair Shop", "Showroom"],
    "nascar": ["Race Track", "Speedway", "Garage", "Man Cave", "Repair Shop", "Showroom"],
    "gasoil": ["Gas Station", "Gas Pump", "Service Station", "Filling Station", "Garage", "Repair Shop"],
    "pinup": ["Garage", "Man Cave", "Bar", "Game Room", "Barber Shop", "Diner"],
    "thermo": ["Gas Station", "Garage", "Storefront", "Shop", "Man Cave", "Porch"],
    "soda": ["Diner", "Soda Fountain", "General Store", "Restaurant", "Kitchen", "Man Cave"],
    "other": ["Garage", "Man Cave", "Shop", "Den", "Game Room", "Storefront"],
}

FEATURES = ["Porcelain Enamel", "Single Sided", "Original Vintage",
            "Collectible", "Wall Display", "Easel Display"]

BIKER_BASE = {
    "Type of Advertising": "Sign",
    "Featured Refinements": "Porcelain Sign",
    "Color": "Multi-color",
    "Theme": "Motorcycle",
    "Characteristics": ["Retro", "Americana", "Nostalgic", "Classic", "Vintage", "Biker", "Chopper"],
    "Room for Display": ["Man Cave", "Garage", "Shop", "Den", "Basement", "Shed", "Game Room"],
    "Country of Origin": "United States",
    "Original/Reproduction": "Original",
}

# The inventory also contains video games, comics and trading cards. Pricing
# those as signs would be destructive, so they are excluded explicitly.
NOT_A_SIGN = re.compile(
    r"\b(PLAYSTATION|PS[1-5]\b|XBOX|NINTENDO|WII|GAMECUBE|SEGA|CONSOLE|CONTROLLER|"
    r"COMIC|TRADING CARD|CARD LOT|BOOSTER|FUNKO|DVD|BLU-?RAY)\b"
)


def is_sign(title):
    t = title.upper()
    return ("PORCELAIN" in t or "PUMP PLATE" in t) and not NOT_A_SIGN.search(t)


def eligible_for_refresh(item):
    """Auctions are excluded outright.

    Their price is a deliberate opening bid, not a catalogue price, so
    repricing one to a tier would destroy the seller's intent — and ending an
    auction that already has bids is worse still. The tiers, the end/relist
    cycle and the store categories are all fixed-price concepts.
    """
    return (
        item["type"] == "FixedPriceItem"
        and is_sign(item["title"])
        and item["watch"] < WATCH_SKIP
        and item["bids"] == 0
    )


def bucket(title):
    t = title.upper()
    if "DAVID MANN" in t or "ED ROTH" in t or "BIKER" in t:
        return "biker"
    if "THERMOMETER" in t:
        return "thermo"
    if "NASCAR" in t or "RACING" in t or "DAYTONA" in t:
        return "nascar"
    if "PIN UP" in t or "PINUP" in t or "MARILYN" in t:
        return "pinup"
    if re.search(r"\b(COLA|SODA|PEPSI|COFFEE|DAIRY|BEER|ROOT|7 ?UP|DINER)\b", t):
        return "soda"
    if "PUMP PLATE" in t or "GAS & OIL" in t or "GASOLINE" in t:
        return "gasoil"
    return "other"


def net(price):
    """After the blended marketplace take, the shipping label and cost basis."""
    return round(0.8533 * price - 32, 2)


def active_listings():
    out, page = [], 1
    while True:
        ack, xml, _ = eb.call(
            "GetMyeBaySelling",
            "<ActiveList><Include>true</Include><DetailLevel>ReturnAll</DetailLevel>"
            f"<Pagination><EntriesPerPage>200</EntriesPerPage><PageNumber>{page}</PageNumber>"
            "</Pagination></ActiveList>",
        )
        if ack not in ("Success", "Warning"):
            break
        for chunk in re.findall(r"<Item>(.*?)</Item>", xml, re.S):
            out.append({
                "id": eb.tag(chunk, "ItemID"),
                "title": eb.tag(chunk, "Title"),
                "start": eb.tag(chunk, "StartTime")[:10],
                "watch": int(eb.tag(chunk, "WatchCount", "0") or 0),
                "bids": int(eb.tag(chunk, "BidCount", "0") or 0),
                "price": float(eb.tag(chunk, "CurrentPrice", "0") or 0),
                # "FixedPriceItem" or "Chinese" (an auction).
                "type": eb.tag(chunk, "ListingType"),
            })
        total = int(eb.tag(xml, "TotalNumberOfPages", "1") or 1)
        if page >= total:
            break
        page += 1
    return out


def attention_report(listings):
    """Everything that needs a human, gathered before anything is changed."""
    lines = []

    signs = [x for x in listings if is_sign(x["title"])]
    auctions = [x for x in listings if x["type"] != "FixedPriceItem"]
    lines.append(f"Active listings: {len(listings)} ({len(signs)} signs)")
    if auctions:
        lines.append(f"Auctions left untouched: {len(auctions)}")

    ack, xml, _ = eb.call(
        "GetMyeBaySelling",
        "<SellingSummary><Include>true</Include></SellingSummary>",
    )
    if ack in ("Success", "Warning"):
        for label, tagname in (("Auctions with bids", "AuctionBidCount"),
                               ("Items sold", "SoldDurationInDays")):
            value = eb.tag(xml, tagname)
            if value:
                lines.append(f"{label}: {value}")

    ack, xml, _ = eb.call(
        "GetOrders",
        "<NumberOfDays>3</NumberOfDays><OrderStatus>Completed</OrderStatus>",
    )
    if ack in ("Success", "Warning"):
        orders = re.findall(r"<Order>(.*?)</Order>", xml, re.S)
        unshipped = [o for o in orders if "<ShippedTime>" not in o]
        lines.append(f"Orders in the last 3 days: {len(orders)}"
                     + (f"  —  {len(unshipped)} NOT YET SHIPPED" if unshipped else ""))
        for o in unshipped[:10]:
            lines.append(f"   ship: {eb.tag(o, 'Title')[:52]}  "
                         f"${eb.tag(o, 'Total')}  to {eb.tag(o, 'BuyerUserID')}")

    ack, xml, _ = eb.call("GetMyMessages", "<DetailLevel>ReturnSummary</DetailLevel>")
    if ack in ("Success", "Warning"):
        unread = eb.tag(xml, "NewMessageCount", "0")
        if unread and unread != "0":
            lines.append(f"UNREAD buyer messages: {unread}")

    return lines


def refresh_one(item):
    """end -> relist -> clear stale best offer -> price, category, specifics."""
    item_id = item["id"]
    title, specifics = eb.read_item(item_id)
    if specifics is None:
        return {"id": item_id, "status": "read failed"}

    ack, _, errs = eb.call(
        "EndFixedPriceItem",
        f"<ItemID>{eb.xe(item_id)}</ItemID><EndingReason>NotAvailable</EndingReason>",
    )
    if ack not in ("Success", "Warning"):
        return {"id": item_id, "status": "end failed",
                "detail": [e[2] for e in errs][:1]}

    ack, xml, errs = eb.call(
        "RelistFixedPriceItem", f"<Item><ItemID>{eb.xe(item_id)}</ItemID></Item>"
    )
    new_id = eb.tag(xml, "ItemID")
    if not new_id or ack not in ("Success", "Warning"):
        # Recoverable: the listing sits in Inactive with a working Relist button.
        return {"id": item_id, "status": "relist FAILED — sitting in Inactive",
                "detail": [e[2] for e in errs][:1]}

    # A relist restores the old best-offer thresholds, which will reject the
    # new lower price with error 22003. Toggling best offer off clears them.
    eb.call("ReviseFixedPriceItem",
            f"<Item><ItemID>{new_id}</ItemID><BestOfferDetails>"
            "<BestOfferEnabled>false</BestOfferEnabled></BestOfferDetails></Item>")

    kind = bucket(title)
    merged = dict(specifics)
    if kind == "biker":
        merged.setdefault(
            "Brand", "Easyriders" if re.search(r"EASY\s?RIDERS", title, re.I) else "Unbranded"
        )
        for k, v in BIKER_BASE.items():
            merged.setdefault(k, v)
    merged["Sign Usage"] = USAGE[kind]
    merged["Sign Features"] = FEATURES

    price = max(PRICE[kind], FLOOR)
    ack, _, errs = eb.call(
        "ReviseFixedPriceItem",
        f"<Item><ItemID>{new_id}</ItemID>"
        f'<StartPrice currencyID="USD">{price:.2f}</StartPrice>'
        "<BestOfferDetails><BestOfferEnabled>true</BestOfferEnabled></BestOfferDetails>"
        f"<Storefront><StoreCategoryID>{STORE_CATEGORY[kind]}</StoreCategoryID></Storefront>"
        + eb.specifics_xml(merged) + "</Item>",
    )
    hard = [e for e in errs if e[0] == "Error"]
    return {
        "id": item_id, "new_id": new_id, "bucket": kind, "price": price,
        "specifics": len(merged),
        "status": "ok" if not hard else "relisted but not fully restored",
        "detail": [e[2] for e in hard][:1],
    }


def main():
    listings = active_listings()
    if not listings:
        print("Could not read any active listings — stopping without changing anything.")
        return 1

    print("== needs attention ==")
    for line in attention_report(listings):
        print(line)

    eligible = [x for x in listings if eligible_for_refresh(x)]
    eligible.sort(key=lambda x: x["start"])
    todo = eligible[:BATCH]

    print(f"\n== refreshing {len(todo)} of {len(eligible)} eligible ==")
    results = []
    for item in todo:
        result = refresh_one(item)
        results.append(result)
        mark = "ok  " if result["status"] == "ok" else "FAIL"
        print(f"{mark} {item['start']}  {item['title'][:46]}  {result['status']}")
        time.sleep(0.4)

    good = sum(1 for r in results if r["status"] == "ok")
    print(f"\nrefreshed {good}/{len(todo)}")
    for r in results:
        if r["status"] != "ok":
            print("  needs a look:", json.dumps(r))

    with open("heartbeat.txt", "w") as fh:
        fh.write(f"last run {datetime.datetime.utcnow().isoformat()}Z — "
                 f"refreshed {good}/{len(todo)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
