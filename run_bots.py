"""Both eBay bots, one entry point, for the twice-daily GitHub Actions job.

  msg_bot     - answers buyer questions that can be read off the listing's own
                data (size, shipping, returns, combining, international) and
                escalates everything touching condition, authenticity or price
  thanks_bot  - one thank-you per SIGN order, naming the collecting theme and
                asking what else the buyer is hunting for

WHY THIS RUNS ON GITHUB ACTIONS AND NOT AS A SCHEDULED CLAUDE TASK
    It used to be a Claude scheduled task. Every firing started a fresh
    container where the bot files did not exist, so the run reported SUCCEEDED
    in twenty seconds having sent nothing. Twice a day, for twelve days, with
    notifications off - indistinguishable from a quiet week. Here the code is
    checked out with the job, so there is nothing to be absent.

Neither bot is allowed to take the other down: each runs in its own
try/except so a failure in one still lets the other do its job, and the
traceback is printed rather than swallowed.

Exit code is 0 unless BOTH bots raised - a partial failure still needs the
thanked.json commit to happen, or the surviving bot repeats itself next run.

Run with --apply to send for real; no args is a dry run of both.
"""
import os
import sys
import traceback

APPLY = "--apply" in sys.argv
SUMMARY = os.environ.get("GITHUB_STEP_SUMMARY")
_lines = []


def note(line):
    """Goes to the log and, on Actions, to the job summary page."""
    print(line, flush=True)
    _lines.append(line)


def stage(name, fn):
    mode = "LIVE" if APPLY else "dry run"
    print(f"\n{'=' * 58}\n{name}  ({mode})\n{'=' * 58}", flush=True)
    try:
        return fn(apply=APPLY), None
    except Exception as exc:
        print(f"!! {name} failed:", flush=True)
        traceback.print_exc()
        return None, f"{type(exc).__name__}: {exc}"


def main():
    import msg_bot
    import thanks_bot

    _, e1 = stage("buyer questions", msg_bot.run)
    _, e2 = stage("post-purchase thank-you", thanks_bot.run)

    note(f"### eBay bots ({'live' if APPLY else 'dry run'})")
    note("")
    note(f"- buyer questions: {'FAILED - ' + e1 if e1 else 'ok'}")
    note(f"- thank-you messages: {'FAILED - ' + e2 if e2 else 'ok'}")
    note("")
    note("Full counts and any escalated buyer questions are in the step log above.")

    if SUMMARY:
        with open(SUMMARY, "a") as fh:
            fh.write("\n".join(_lines) + "\n")

    print("\ndone", flush=True)
    if e1 and e2:
        sys.exit(1)


if __name__ == "__main__":
    main()
