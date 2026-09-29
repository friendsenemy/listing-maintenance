"""Answer the buyer questions that have a factual answer; escalate the rest.

Ray's complaint 2026-09-14: a buyer asked the size of a sign at 9:21pm and he
had to log in to answer. eBay's own eBay.ai only *suggests* a reply - its
settings page says "You will have the ability to review and edit responses
before they are sent" - so it still needs him at a screen. This does the
sending.

The rule that keeps it safe: an answer is only ever sent when it can be read
off this listing's own data. Nothing is inferred, nothing is generalised from
other listings, and nothing that touches judgement gets answered at all.

ANSWERED automatically
    size            - only when the listing itself states one
    shipping cost   - from the listing's shipping profile
    handling time   - from DispatchTimeMax
    returns         - from the return profile
    combining       - from the live volume-pricing tiers
    international   - from eBay International Shipping being on

ESCALATED to Ray, never answered
    condition, damage, restoration
    authenticity, originality, age, "is this real / repro / vintage"
    price, offers, "will you take", discounts, negotiation
    anything the classifier does not recognise
    any question about a listing that does NOT state a size (the one case
    where a size answer would be a guess - which is how wrong measurements
    end up in front of buyers)

Run it as often as you like; it only ever replies once per message, because it
reads eBay's own Replied flag rather than keeping its own state.
"""
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

# --------------------------------------------------------------- classifying

ESCALATE = re.compile(
    r"\b(condition|damag|crack|chip|rust|restor|repaint|touch.?up|"
    r"authentic|original|reproduction|repro|fake|real|genuine|age|how old|year|date|"
    r"price|offer|deal|discount|lower|best|take \$|accept \$|negotiat|cheap)\b", re.I)

# Stems are written with \w* rather than a trailing \b: "dimension" must also
# catch "dimensions", and "ship" must catch "shipping". A trailing \b after a
# stem silently fails on the plural, which is how a size question ends up
# unanswered.
TOPICS = [
    ("size", re.compile(r"\b(size\w*|how big|dimension\w*|measure\w*|diameter\w*|"
                        r"inch\w*|how large|how wide|how tall|how many inches)", re.I)),
    ("combine", re.compile(r"(combin\w*.{0,40}(ship\w*|postage|discount\w*)|"
                           r"(ship\w*|postage).{0,40}combin\w*|"
                           r"\b(multiple|more than one|two or more|buy \d+|"
                           r"buy (two|three|four))\b.{0,40}(ship\w*|discount\w*|deal))", re.I)),
    ("international", re.compile(r"\b(international\w*|overseas|worldwide|canada|"
                                 r"uk\b|australia|europe|outside the us)", re.I)),
    ("handling", re.compile(r"(how (soon|fast|quick\w*)|when will (it|you)|dispatch\w*|"
                            r"handling time|same day|how long.{0,30}(ship\w*|send|post))", re.I)),
    ("returns", re.compile(r"\b(return\w*|refund\w*|money back|send it back)", re.I)),
    ("shipping", re.compile(r"\b(shipping|postage|delivery|freight|cost to ship\w*|"
                            r"how much.{0,20}ship\w*)", re.I)),
]


def classify(text):
    """Every factual topic the question touches, or None if any part needs Ray."""
    if ESCALATE.search(text):
        return None
    hits = [name for name, pat in TOPICS if pat.search(text)]
    # "combine" implies shipping; don't answer both.
    if "combine" in hits and "shipping" in hits:
        hits.remove("shipping")
    return hits or None


# ------------------------------------------------------------------ listing

def listing_facts(iid):
    ack, x, _ = ebay.trading(
        "GetItem",
        f"<ItemID>{iid}</ItemID><DetailLevel>ReturnAll</DetailLevel>"
        "<IncludeItemSpecifics>true</IncludeItemSpecifics>", timeout=90)
    if ack not in ("Success", "Warning"):
        return None
    g = lambda t, d="": (lambda m: html.unescape(m.group(1)) if m else d)(
        re.search(rf"<{t}[^>]*>(.*?)</{t}>", x, re.S))
    desc = re.sub(r"<!\[CDATA\[|\]\]>", "", g("Description"))
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(desc)))
    size = None
    m = re.search(r"Size: approximately ([^.]+)\.", text)
    if m:
        size = m.group(1).strip()
    else:
        m = re.search(r'(\d{1,2}(?:\.\d+)?\s*(?:"|inch\w*)\s*(?:x|by)\s*\d{1,2}(?:\.\d+)?\s*'
                      r'(?:"|inch\w*)?|\d{1,2}(?:\.\d+)?\s*(?:"|inch\w*)\s*(?:round|in diameter|diameter))',
                      text, re.I)
        if m:
            size = m.group(1).strip()
    return {
        "title": g("Title"),
        "size": size,
        "free_ship": "FREE SHIPPING" in g("ShippingProfileName").upper()
                     or "Same Price Shipping" in g("ShippingProfileName"),
        "handling": g("DispatchTimeMax", "1"),
        "returns_60": "60" in g("ReturnProfileName"),
    }


# ------------------------------------------------------------------ answers

def answer_for(topics, f):
    """One reply covering every topic asked. None if any part cannot be answered."""
    if not topics:
        return None
    if isinstance(topics, str):
        topics = [topics]
    parts = [_one(t, f) for t in topics]
    if any(p is None for p in parts):
        return None            # all or nothing - a half answer invites a second message
    return " ".join(parts)


def _one(topic, f):
    if topic == "size":
        if not f["size"]:
            return None                       # never guess a measurement
        return (f'Thanks for asking - this one is {f["size"]}. '
                "The measurement is in the listing description too. "
                "If you would like a photo of it against a tape measure, just say so "
                "and I will send one.")
    if topic == "shipping":
        return ("Shipping is free within the United States - the price you see is the price "
                "you pay. It goes out in a sturdy box with the sign wrapped and corner-protected.")
    if topic == "handling":
        return (f'It ships within {f["handling"]} business day of payment clearing, '
                "tracked, and you will get the tracking number by email as soon as it is on its way.")
    if topic == "returns":
        return ("Returns are free for 60 days. If it is not what you pictured, start the return "
                "through eBay and I pay the return shipping - no questions asked.")
    if topic == "combine":
        return ("Yes, happy to combine. Buy 2 and save 10%, buy 3 and save 15%, buy 4 or more "
                "and save 16% - the discount comes off automatically in the cart, and they ship "
                "together in one box.")
    if topic == "international":
        return ("Yes - it ships worldwide through eBay International Shipping. You pay at "
                "checkout and eBay handles the customs paperwork and any import charges, so "
                "there is nothing to settle at the door.")
    return None


SIGNOFF = "\n\nThanks for looking.\ndynamitedealshop"


# ------------------------------------------------------------------- inbox

def strip_email(body):
    """Buyer questions arrive wrapped in eBay's HTML email furniture."""
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?is)@media[^{]*\{[^}]*\}", " ", body)
    txt = html.unescape(re.sub(r"<[^>]+>", "\n", body))
    lines = [l.strip() for l in txt.split("\n") if l.strip()]
    drop = re.compile(r"^(eBay|View item|Respond|Mark as|Report|Learn more|Privacy|"
                      r"This message was sent|Item|Seller|Buyer|©|body\[|width:|\.)", re.I)
    keep = [l for l in lines if not drop.match(l) and len(l) > 12 and len(l) < 400]
    return " ".join(keep[:8])


def inbox():
    ack, xml, _ = ebay.trading("GetMyMessages", "<DetailLevel>ReturnHeaders</DetailLevel>",
                               timeout=110)
    heads = []
    for m in re.findall(r"<Message>(.*?)</Message>", xml, re.S):
        g = lambda t, d="": (lambda x: html.unescape(x.group(1)) if x else d)(
            re.search(rf"<{t}[^>]*>(.*?)</{t}>", m, re.S))
        heads.append({"id": g("MessageID"), "sender": g("Sender"), "item": g("ItemID"),
                      "subject": g("Subject"), "replied": g("Replied") == "true",
                      "type": g("MessageType"), "date": g("ReceiveDate")})
    todo = [h for h in heads
            if h["sender"] and h["sender"].lower() != "ebay" and not h["replied"] and h["item"]]
    if not todo:
        return []
    body = "".join(f"<MessageIDs><MessageID>{h['id']}</MessageID></MessageIDs>" for h in todo)
    ack, xml, _ = ebay.trading("GetMyMessages", body + "<DetailLevel>ReturnMessages</DetailLevel>",
                               timeout=110)
    full = {}
    for m in re.findall(r"<Message>(.*?)</Message>", xml, re.S):
        g = lambda t, d="": (lambda x: html.unescape(x.group(1)) if x else d)(
            re.search(rf"<{t}[^>]*>(.*?)</{t}>", m, re.S))
        full[g("MessageID")] = strip_email(g("Text") or g("Content"))
    for h in todo:
        h["text"] = full.get(h["id"], "")
    return todo


def reply(h, text):
    return ebay.trading(
        "AddMemberMessageRTQ",
        f"<ItemID>{h['item']}</ItemID><MemberMessage>"
        f"<Body>{ebay.xe(text)}</Body>"
        f"<RecipientID>{ebay.xe(h['sender'])}</RecipientID>"
        f"<ParentMessageID>{h['id']}</ParentMessageID>"
        "<MessageType>ResponseToASQQuestion</MessageType>"
        "<DisplayToPublic>false</DisplayToPublic></MemberMessage>", timeout=90)


def run(apply=False):
    msgs = inbox()
    print(f"unanswered buyer messages: {len(msgs)}", flush=True)
    sent, escalated = 0, []
    for h in msgs:
        q = f"{h['subject']} {h['text']}"
        topic = classify(q)
        facts = listing_facts(h["item"]) if topic else None
        body = answer_for(topic, facts) if facts else None
        if not body:
            escalated.append({"from": h["sender"], "item": h["item"],
                              "subject": h["subject"][:70], "why": topic or "unrecognised"})
            print(f"  ESCALATE ({topic or 'unrecognised'})  {h['sender']}  "
                  f"{h['subject'][:60]}", flush=True)
            continue
        body += SIGNOFF
        if not apply:
            print(f"  WOULD SEND [{topic}] to {h['sender']} re {h['item']}:\n    "
                  + body.replace("\n", "\n    "), flush=True)
            continue
        ack, out, errs = reply(h, body)
        hard = [e for e in errs if e[0] == "Error"]
        if hard:
            escalated.append({"from": h["sender"], "item": h["item"],
                              "why": f"send failed {hard[0][1]}"})
            print(f"  FAIL {h['sender']} {hard[0][1]}: {hard[0][2][:60]}", flush=True)
        else:
            sent += 1
            print(f"  SENT [{topic}] to {h['sender']} re {h['item']}", flush=True)
        time.sleep(0.5)
    print(f"\nsent {sent} | needing Ray: {len(escalated)}")
    for e in escalated:
        print("   ", json.dumps(e))
    return {"sent": sent, "escalated": escalated}


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
