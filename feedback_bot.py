"""Reply to every POSITIVE feedback left for us as a seller.

Rules, in order:
  1. CommentType must be Positive. Neutral and Negative are never touched by
     this bot - those need a human, and a canned "thanks!" under a complaint
     reads as tone-deaf.
  2. Role must be Seller (feedback left for us as the seller, not feedback we
     received as a buyer).
  3. Skip eBay's own automated feedback ("Order delivered on time with no
     issues", "Order completed successfully", masked user id). It is hidden on the public wall by default and
     a thank-you under a robot comment looks like a robot.
  4. Skip anything that already carries a FeedbackResponse. eBay stores the
     reply on the feedback record itself, so there is no dedupe file to keep
     in sync and no way to double-reply.

The reply is picked at random from a pool written in Ray's own voice (his
hand-typed replies were the template). eBay caps a feedback reply at 80
characters, so every line in the pool is asserted under that.

run(apply=False) is the entry point run_bots.py calls. Returns a dict of counts.
"""
import random
import re
import time

import ebay_client as eb

REPLIES = [
    "Thank you for the kind words! We look forward to doing business with you again.",
    "That was very nice of you to say - hope to see you back in the store soon!",
    "Thank you so much, we really appreciate it. Enjoy the sign!",
    "Thanks for the great feedback! Always happy to help a fellow collector.",
    "Much appreciated! Glad it arrived safe and you like it. Come back anytime.",
    "Thank you! Buyers like you make this fun. Hope it looks great on the wall.",
    "We appreciate you! Thanks for the kind review and for shopping with us.",
    "Thanks so much! If you ever hunt for another sign, message us first.",
    "Really appreciate the feedback. Enjoy it, and thanks for backing a small shop!",
    "Thank you for the kind review - it means a lot to a family shop like ours.",
    "Glad you are happy with it! Thanks for the feedback and for your business.",
    "Thanks a bunch! Great to deal with you - hope to do it again soon.",
]
for _r in REPLIES:
    assert len(_r) <= 80, (len(_r), _r)

PAGE = 200

# eBay's own system feedback. RespondToFeedback refuses these with error
# 17767 "not supported", so they must be filtered out or retried forever.
_AUTO_PREFIXES = ("order delivered on time", "order completed successfully")


def _is_auto(text):
    t = text.strip().lower()
    return any(t.startswith(p) for p in _AUTO_PREFIXES)


def _fetch_all():
    """Every feedback received, newest first, across all pages."""
    out, page = [], 1
    while True:
        ack, xml, errors = eb.call(
            "GetFeedback",
            "<DetailLevel>ReturnAll</DetailLevel>"
            "<FeedbackType>FeedbackReceived</FeedbackType>"
            f"<Pagination><EntriesPerPage>{PAGE}</EntriesPerPage>"
            f"<PageNumber>{page}</PageNumber></Pagination>",
        )
        if ack not in ("Success", "Warning"):
            raise RuntimeError(f"GetFeedback page {page}: {ack} {errors}")
        for d in re.findall(r"<FeedbackDetail>(.*?)</FeedbackDetail>", xml, re.S):
            g = lambda t: eb.tag(d, t)
            out.append({
                "id": g("FeedbackID"), "item": g("ItemID"), "user": g("CommentingUser"),
                "type": g("CommentType"), "role": g("Role"),
                "responded": "<FeedbackResponse>" in d,
                "text": g("CommentText")[:60], "when": g("CommentTime")[:10],
                "automated": ("***" in g("CommentingUser")
                              or _is_auto(g("CommentText"))),
            })
        pages = int(eb.tag(xml, "TotalNumberOfPages", "1") or 1)
        if page >= pages or not re.search(r"<FeedbackDetail>", xml):
            break
        page += 1
    return out


def run(apply=False):
    fb = _fetch_all()
    todo = [f for f in fb if f["type"] == "Positive" and f["role"] == "Seller"
            and not f["responded"] and not f["automated"]
            and f["id"] and f["item"] and f["user"]]
    automated = sum(1 for f in fb if f["automated"])
    skipped_nonpos = sum(1 for f in fb if f["type"] != "Positive")
    print(f"feedback received: {len(fb)}  | positive+seller+unanswered: {len(todo)}"
          f"  | neutral/negative left alone: {skipped_nonpos}"
          f"  | eBay automated skipped: {automated}", flush=True)
    sent = failed = 0
    for f in todo:
        reply = random.choice(REPLIES)
        line = f"  {f['when']} {f['user']:<18} {f['item']}  {f['text']!r}"
        if not apply:
            print(f"{line}\n      -> would reply: {reply}", flush=True)
            continue
        ack, xml, errors = eb.call(
            "RespondToFeedback",
            f"<FeedbackID>{eb.xe(f['id'])}</FeedbackID>"
            f"<ItemID>{eb.xe(f['item'])}</ItemID>"
            f"<TargetUserID>{eb.xe(f['user'])}</TargetUserID>"
            "<ResponseType>Reply</ResponseType>"
            f"<ResponseText>{eb.xe(reply)}</ResponseText>",
        )
        if ack in ("Success", "Warning"):
            sent += 1
            print(f"{line}\n      replied: {reply}", flush=True)
        else:
            failed += 1
            print(f"{line}\n      !! {ack} {errors}", flush=True)
        time.sleep(0.4)
    print(f"feedback replies: {'sent' if apply else 'would send'} {len(todo) if not apply else sent}"
          f"{'' if not apply else f', failed {failed}'}", flush=True)
    return {"candidates": len(todo), "sent": sent, "failed": failed}


if __name__ == "__main__":
    import sys
    run(apply="--apply" in sys.argv)
