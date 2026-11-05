#!/usr/bin/env python3
"""Cost, requests and errors from the deAPI usage reports. Standard library only (Python 3.9+).

    export DEAPI_API_KEY=sk-dp-...
    python usage_report.py --group-by model --days 30            # the calling key (any role)
    python usage_report.py --account --group-by key --days 7     # whole account (ADMIN key)
    python usage_report.py --json                                # raw report

Read-only and free: GET /api/v2/key, /api/v2/key/usage and /api/v2/account/usage.
"""

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "deapi-example/2.0 (+https://docs.deapi.ai)"


def checked_api_url(url):
    """Allow only https://*.deapi.ai, or plain http on this machine for tests and mocks."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if (parts.scheme == "https" and (host == "deapi.ai" or host.endswith(".deapi.ai"))) or \
            (parts.scheme == "http" and host in ("127.0.0.1", "localhost")):
        return url.rstrip("/")
    raise ValueError("refusing to send the API key to %r" % host)


def get(api_url, key, path, query=None):
    url = api_url + path + ("?" + urllib.parse.urlencode(query) if query else "")
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": USER_AGENT})
    # Unredirected: urllib would copy a normal header to a redirect target.
    req.add_unredirected_header("Authorization", "Bearer " + key)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def usage(api_url, key, account=False, group_by="day", days=7, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    path = "/api/v2/account/usage" if account else "/api/v2/key/usage"
    query = {"group_by": group_by, "from": iso(now - datetime.timedelta(days=days)), "to": iso(now)}
    return get(api_url, key, path, query)["data"]


def render(report, key_info=None):
    lines = []
    if key_info:
        limit = key_info.get("limit")
        lines.append("key %s (%s, %s)%s" % (
            key_info.get("name"), key_info.get("role"), key_info.get("status"),
            "" if limit is None else ", limit %s %s, %s left" % (
                limit, key_info.get("reset_interval"), key_info.get("limit_remaining"))))
    currency = report.get("currency", "USD")
    lines.append("%s .. %s, by %s" % (report.get("from"), report.get("to"), report.get("group_by")))
    lines.append("%-36s %12s %9s %7s" % ("group", "cost " + currency, "requests", "errors"))
    groups = sorted(report.get("groups") or [], key=lambda g: -(g.get("cost") or 0))
    for group in groups:
        label = group.get("name") or group.get("key")
        lines.append("%-36s %12.6f %9d %7d" % (str(label)[:36], group.get("cost") or 0,
                                              group.get("requests") or 0, group.get("errors") or 0))
    totals = report.get("totals") or {}
    lines.append("%-36s %12.6f %9d %7d" % ("total", totals.get("cost") or 0,
                                          totals.get("requests") or 0, totals.get("errors") or 0))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="deAPI usage report")
    parser.add_argument("--account", action="store_true", help="whole account (ADMIN key)")
    parser.add_argument("--group-by", default="day", choices=["day", "hour", "type", "model", "key"])
    parser.add_argument("--days", type=int, default=7, help="1-90 (hourly: up to 31)")
    parser.add_argument("--json", action="store_true", help="print the raw report")
    args = parser.parse_args(argv)
    if args.group_by == "key" and not args.account:
        parser.error("--group-by key needs --account")
    if not 1 <= args.days <= 90:
        parser.error("--days must be 1-90")

    key = os.environ.get("DEAPI_API_KEY", "")
    if not key:
        print("error: DEAPI_API_KEY is not set", file=sys.stderr)
        return 2
    try:
        api_url = checked_api_url(os.environ.get("DEAPI_API_URL", "https://api.deapi.ai"))
        report = usage(api_url, key, args.account, args.group_by, args.days)
        key_info = None if args.account else get(api_url, key, "/api/v2/key").get("data")
    except urllib.error.HTTPError as err:
        try:
            code = json.loads(err.read().decode("utf-8")).get("code")
        except ValueError:
            code = None
        if code == "insufficient_permissions":
            print("error: the account report needs an ADMIN key; without --account the report "
                  "covers the calling key", file=sys.stderr)
        else:
            print("error: HTTP %s %s" % (err.code, code or ""), file=sys.stderr)
        return 1
    except (ValueError, urllib.error.URLError) as err:
        print("error: %s" % err, file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2) if args.json else render(report, key_info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
