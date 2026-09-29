"""Message every buyer who bought a SIGN, once, and ask what else they hunt for.

WHY GetOrders AND NOT A LISTING WATCHER
    Ray's requirement 2026-09-17: fire "whether it be through Buy It Now,
    Auction or Offers or any other means". Every one of those paths ends in an
    Order - a BIN, a won auction and an accepted Best Offer are identical at
    the Order level - so polling GetOrders catches all of them with no
    per-path special casing. Watching listings or Best Offers instead would
    need three code paths and would still miss a fourth.

WHY THEME AND NOT BRAND
    Ray's first instinct was "we have more of this BRAND". His own 90 days of
    orders say otherwise. The repeat buyers cluster by SUBJECT:
      - one bought 6 pin-up signs across Ford, Cadillac, Playboy and two
        unbranded ones - five different brands, one subject
      - one bought Jackie Robinson, Ted Williams and Mickey Mantle in three
        separate orders - three different teams, one subject
      - one bought the same 1956 Ford Marilyn design twice, nine days apart
    So the message names the theme, not the marque.

SIGNS ONLY
    The store also sells PS3/PS4/DS game lots. A buyer is messaged only when
    the item's category is a sign category AND the title looks like a sign.
    Both must pass. A missed message costs nothing; a "more signs like this?"
    note to someone who bought a games lot looks careless.

NEVER TWICE
    Orders already messaged are recorded in thanked.json, and a buyer is not
    messaged again for 30 days no matter how many orders they place. The
    six-signs-in-two-orders buyer should get one note, not two.

STAYS ON EBAY
    The copy asks them to reply on eBay and offers to LIST the item. It never
    names a website, an email or a phone number. eBay's policy on offering to
    buy or sell outside eBay treats contact details obtained from a
    transaction as off limits, and the penalty is account suspension.

Run with no args for a dry run (prints exactly what would be sent).
Run with --apply to actually send.
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time

import ebay_client as _c


class _Ebay:
    """Adapter onto the repo's ebay_client, which is the same Trading API
    wrapper with a different name and no timeout argument (it retries with
    backoff instead)."""
    xe = staticmethod(_c.xe)
    specifics_xml = staticmethod(_c.specifics_xml)

    @staticmethod
    def trading(name, inner, timeout=None):
        return _c.call(name, inner)


ebay = _Ebay()

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thanked.json")
LOOKBACK_DAYS = 7          # orders newer than this are considered.
                           # Wider than the twice-daily cadence needs, so a
                           # missed run does not lose a buyer permanently -
                           # thanked.json is what prevents duplicates, not
                           # this window.
BUYER_COOLDOWN_DAYS = 30
SIGN_CATEGORIES = {
    "35684",    # Gas & Oil > Merch & Memorabilia > Signs
    "10806",    # Signs > Original > 1930-69
    "10805",    # Signs > Reproduction
    "10809",    # Soda > Orange Crush
    "35698",    # Signs (other)
    "50130",    # Signs
    "259104",
    "183454",
}
SIGN_WORDS = re.compile(r"\b(sign|porcelain|pump plate|plate)\b", re.I)

# Most specific first: "TEXACO MARINE MOTOR OIL POPEYE" is a Popeye sign to a
# collector, not a marine sign, so CARTOON has to be tested before MARINE.
THEMES = [
    ("pin-up and cheesecake signs",
     r"pin.?up|marilyn|monroe|playboy|bettie|betty page|burlesque|cheesecake"),
    ("cartoon character signs",
     r"mickey(?!\s+mantle)|minnie|disney|popeye|donald duck|betty boop|koko|snoopy|gumby|"
     r"felix|olive oyl|clown"),
    ("racing and NASCAR signs",
     r"nascar|stock car|speedway|daytona|talladega|darlington|racing|grand national"),
    ("vintage baseball and sports signs",
     r"baseball|ballpark|yankees\b|dodger|red ?sox|redlegs|\breds\b|cardinals|"
     r"braves|\bcubs\b|tigers|pirates|phillies|orioles|\bmets\b|\bgiants\b|"
     r"indians|senators|athletics|white sox|brewers|negro league|"
     r"mantle|maris|ted williams|jackie robinson|babe ruth|dimaggio|musial|"
     r"hall of fame|world series|cooperstown|quaker bread|"
     r"football|\bnfl\b|\bmlb\b|\bnba\b|basketball|hockey|\bnhl\b"),
    ("motorcycle and biker signs",
     r"harley|davidson|motorcycle|biker|david mann|easyrider|indian motocycle|"
     r"indian motorcycle|knucklehead|panhead"),
    ("national park and outdoors signs",
     r"national park|state park|provincial park|forest service|park service|"
     r"ranger|yellowstone|yosemite|glacier national|grand canyon|wildlife|"
     r"fish.{0,3}wildlife|smokey"),
    ("hunting and fishing signs",
     r"hunt|fishing|winchester|remington|\bduck\b|bait|tackle|sportsman|"
     r"shotgun|outdoorsman|trout|angler"),
    ("aviation signs",
     r"aviation|aircraft|airplane|flying tiger|p-?40|pilot|airway|aero|airline"),
    ("marine and boating signs",
     r"marine|outboard|nautical|\byacht\b|boating|mail port|ship.?s wheel"),
    ("soda and beverage signs",
     r"orange crush|coca.?cola|\bcoke\b|pepsi|\bsoda\b|root beer|7.?up|"
     r"dr pepper|nehi|moxie"),
    ("farm and tractor signs",
     r"john deere|farmall|tractor|allis.?chalmers|international harvester|"
     r"massey|agricultur"),
    ("knife and blade advertising signs",
     r"case xx|\bknives\b|\bknife\b|cutlery|\bblade\b"),
    ("vintage gas and oil signs",
     r"texaco|sinclair|mobil|shell|gulf|esso|standard oil|sunoco|conoco|phillips|"
     r"gasoline|motor oil|pump plate|filling station|service station|petroleum"),
]
THEMES = [(label, re.compile(rx, re.I)) for label, rx in THEMES]
GENERIC = "12-inch porcelain signs"


def theme_for(title):
    for label, rx in THEMES:
        if rx.search(title):
            return label
    return GENERIC


def body_for(title, theme):
    short = re.sub(r"\s+", " ", title).strip()
    if len(short) > 64:
        short = short[:61].rstrip() + "..."
    generic = (theme == GENERIC)
    line2 = ("We keep a lot more than what is listed - only a fraction of what we "
             "have is actually up on eBay at any one time."
             if generic else
             f"We have a good many more {theme} where that one came from, and "
             "most of them are not listed yet.")
    return (
        f"Hi, and thank you for the order - the {short} is packed and going out "
        "within one business day.\n\n"
        f"{line2}\n\n"
        "If there is a particular design, brand or subject you are hunting for, "
        "just reply to this message and tell us. We will go through the shelves "
        "and get it listed for you on eBay, and we will send you the link so you "
        "get first look before it goes out to everyone else. No obligation at all "
        "either way.\n\n"
        "Thanks again,\nDynamite Deal Shop"
    )


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"orders": [], "buyers": {}}


def save_state(s):
    json.dump(s, open(STATE, "w"), indent=1)


def recent_orders(days):
    end = dt.datetime.utcnow()
    start = end - dt.timedelta(days=days)
    out, page = [], 1
    while True:
        xml = (f"<CreateTimeFrom>{start:%Y-%m-%dT%H:%M:%S}.000Z</CreateTimeFrom>"
               f"<CreateTimeTo>{end:%Y-%m-%dT%H:%M:%S}.000Z</CreateTimeTo>"
               "<OrderStatus>All</OrderStatus><DetailLevel>ReturnAll</DetailLevel>"
               f"<Pagination><EntriesPerPage>100</EntriesPerPage>"
               f"<PageNumber>{page}</PageNumber></Pagination>")
        ack, res, _ = ebay.trading("GetOrders", xml, timeout=150)
        if ack not in ("Success", "Warning"):
            print(f"GetOrders {ack}", flush=True)
            break
        for o in re.findall(r"<Order>(.*?)</Order>", res, re.S):
            g = lambda t, d="": (lambda m: html.unescape(m.group(1)) if m else d)(
                re.search(rf"<{t}[^>]*>(.*?)</{t}>", o, re.S))
            if g("OrderStatus") in ("Cancelled", "Inactive"):
                continue
            items = [(html.unescape(i), html.unescape(t)) for i, t in
                     re.findall(r"<ItemID>(.*?)</ItemID>.*?<Title>(.*?)</Title>", o, re.S)]
            out.append({"order": g("OrderID"), "buyer": g("BuyerUserID"),
                        "created": g("CreatedTime"), "items": items})
        tot = int((re.search(r"<TotalNumberOfPages>(\d+)", res) or ["", "1"])[1])
        if page >= tot:
            break
        page += 1
    return out


_CAT = {}


def is_sign(item_id, title):
    """Both the category AND the title have to look like a sign."""
    if not SIGN_WORDS.search(title):
        return False
    if item_id not in _CAT:
        ack, xml, _ = ebay.trading(
            "GetItem", f"<ItemID>{item_id}</ItemID><DetailLevel>ReturnAll</DetailLevel>",
            timeout=90)
        m = re.search(r"<PrimaryCategory><CategoryID>(\d+)", xml)
        _CAT[item_id] = m.group(1) if m else None
    return _CAT[item_id] in SIGN_CATEGORIES


def send(item_id, buyer, text):
    return ebay.trading(
        "AddMemberMessageAAQToPartner",
        f"<ItemID>{item_id}</ItemID><MemberMessage>"
        "<Subject>Thank you for your order</Subject>"
        f"<Body>{ebay.xe(text)}</Body>"
        f"<RecipientID>{ebay.xe(buyer)}</RecipientID>"
        "<QuestionType>General</QuestionType>"
        "<EmailCopyToSender>false</EmailCopyToSender></MemberMessage>", timeout=90)


def run(apply=False):
    st = load_state()
    seen = set(st["orders"])
    now = dt.datetime.utcnow()
    orders = recent_orders(LOOKBACK_DAYS)
    print(f"orders in the last {LOOKBACK_DAYS} days: {len(orders)}", flush=True)
    sent = skipped = 0
    for o in sorted(orders, key=lambda x: x["created"]):
        if o["order"] in seen:
            continue
        last = st["buyers"].get(o["buyer"])
        if last:
            age = (now - dt.datetime.strptime(last, "%Y-%m-%dT%H:%M:%S")).days
            if age < BUYER_COOLDOWN_DAYS:
                print(f"  skip {o['buyer'][:4]}*** - messaged {age}d ago", flush=True)
                st["orders"].append(o["order"]); skipped += 1
                continue
        signs = [(i, t) for i, t in o["items"] if is_sign(i, t)]
        if not signs:
            print(f"  skip {o['order']} - no signs in this order "
                  f"({o['items'][0][1][:40] if o['items'] else 'empty'})", flush=True)
            st["orders"].append(o["order"]); skipped += 1
            continue
        item_id, title = signs[0]
        theme = theme_for(" ".join(t for _, t in signs))
        text = body_for(title, theme)
        if not apply:
            print(f"\n  WOULD SEND to {o['buyer'][:4]}***  order {o['order']}")
            print(f"  theme: {theme}  ({len(signs)} sign(s) in order)")
            print("  " + text.replace("\n", "\n  "), flush=True)
            # Mirror the live cooldown so a dry run does not show two messages
            # to a buyer who would only ever receive one.
            st["buyers"][o["buyer"]] = now.strftime("%Y-%m-%dT%H:%M:%S")
            sent += 1
            continue
        ack, res, errs = send(item_id, o["buyer"], text)
        hard = [e for e in errs if e[0] == "Error"]
        if hard:
            print(f"  FAIL {o['order']} {hard[0][1]}: {hard[0][2][:90]}", flush=True)
            continue
        sent += 1
        st["orders"].append(o["order"])
        st["buyers"][o["buyer"]] = now.strftime("%Y-%m-%dT%H:%M:%S")
        print(f"  SENT to {o['buyer'][:4]}***  [{theme}]  order {o['order']}", flush=True)
        time.sleep(0.6)
    if apply:
        save_state(st)
    print(f"\nsent {sent} | skipped {skipped}")


if __name__ == "__main__":
    run("--apply" in sys.argv)
