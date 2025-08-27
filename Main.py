#!/usr/bin/env python3
"""
ical_to_todoist.py

Sync events from an iCalendar (ICS) URL into a Todoist project, with duplicate protection.

Requirements:
    pip install requests icalendar python-dateutil pytz

Usage:
    TODOIST_API_TOKEN="abcd..." python ical_to_todoist.py --ical-url "https://..." --project-id 123456789
"""

import os
import re
import argparse
import requests
from datetime import datetime, date
from dateutil import tz
from dateutil.parser import parse as dateutil_parse
from icalendar import Calendar, Event
from urllib.parse import urlparse

# Config / constants
TODOIST_API_BASE = "https://api.todoist.com/rest/v2"
DEFAULT_MARKER = "ICUID:"  # marker appended to task content for duplicate protection

def isoformat_for_todoist(dt):
    """Return ISO 8601 datetime string with timezone (or date string) appropriate for Todoist.
    If dt is a date (no time) return 'YYYY-MM-DD' (all-day).
    If dt is a datetime, return RFC3339 with timezone offset.
    """
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt.isoformat()  # date-only
    # datetime
    if dt.tzinfo is None:
        # assume UTC if naive (or you could provide a timezone)
        dt = dt.replace(tzinfo=tz.tzutc())
    return dt.isoformat()

def fetch_ics(ical_url, timeout=20):
    r = requests.get(ical_url, timeout=timeout)
    r.raise_for_status()
    return r.content

def parse_calendar(ics_bytes):
    cal = Calendar.from_ical(ics_bytes)
    events = []
    for comp in cal.walk():
        if comp.name == "VEVENT":
            uid = str(comp.get('uid', ''))
            summary = str(comp.get('summary', '')).strip()
            description = str(comp.get('description', '') or '').strip()
            location = str(comp.get('location', '') or '').strip()
            dtstart = comp.get('dtstart').dt if comp.get('dtstart') else None
            dtend = comp.get('dtend').dt if comp.get('dtend') else None
            # handle recurrence? we skip recurring expansion here (basic sync)
            events.append({
                "uid": uid,
                "summary": summary,
                "description": description,
                "location": location,
                "dtstart": dtstart,
                "dtend": dtend,
                "raw": comp
            })
    return events

def todoist_headers(api_token):
    return {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

def list_tasks_for_project(api_token, project_id):
    """Return list of active tasks for the project."""
    url = f"{TODOIST_API_BASE}/tasks"
    params = {"project_id": project_id}
    r = requests.get(url, headers=todoist_headers(api_token), params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def find_existing_tasks_by_uid(tasks, marker=DEFAULT_MARKER):
    """Return dict mapping UID -> task object for tasks that contain the marker."""
    pattern = re.compile(rf"{re.escape(marker)}([^\s)]+)", re.IGNORECASE)
    mapping = {}
    for t in tasks:
        m = pattern.search(t.get("content", ""))
        if m:
            uid = m.group(1).strip()
            mapping[uid] = t
    return mapping

def create_task_for_event(api_token, project_id, event, marker=DEFAULT_MARKER, dry_run=False):
    content_main = event["summary"] or "Untitled event"
    # append location/desc briefly, then marker
    extras = []
    if event["location"]:
        extras.append(f"@{event['location']}")
    if event["description"]:
        # keep first 120 chars of description inline to avoid very long contents
        extras.append((event["description"][:120] + ("..." if len(event["description"])>120 else "")))
    marker_text = f" ({marker}{event['uid']})"
    content = content_main
    if extras:
        content = f"{content} — {' | '.join(extras)}"
    content = content + marker_text

    payload = {
        "content": content,
        "project_id": int(project_id),
    }

    # set due_datetime or due date
    if event["dtstart"] is not None:
        dt = event["dtstart"]
        iso = isoformat_for_todoist(dt)
        # date-only vs datetime
        if isinstance(dt, date) and not isinstance(dt, datetime):
            payload["due_date"] = iso
        else:
            payload["due_datetime"] = iso

    if dry_run:
        print("[DRY RUN] Would create task:", payload)
        return None

    url = f"{TODOIST_API_BASE}/tasks"
    r = requests.post(url, headers=todoist_headers(api_token), json=payload, timeout=20)
    if r.status_code not in (200, 201):
        r.raise_for_status()
    return r.json()

def update_task_for_event(api_token, task_id, event, marker=DEFAULT_MARKER, dry_run=False):
    """Update existing task using Todoist update endpoint. Uses POST to /tasks/{id} per Todoist docs."""
    content_main = event["summary"] or "Untitled event"
    extras = []
    if event["location"]:
        extras.append(f"@{event['location']}")
    if event["description"]:
        extras.append((event["description"][:120] + ("..." if len(event["description"])>120 else "")))
    marker_text = f" ({marker}{event['uid']})"
    content = content_main
    if extras:
        content = f"{content} — {' | '.join(extras)}"
    content = content + marker_text

    payload = {
        "content": content,
    }
    if event["dtstart"] is not None:
        dt = event["dtstart"]
        iso = isoformat_for_todoist(dt)
        if isinstance(dt, date) and not isinstance(dt, datetime):
            payload["due_date"] = iso
            # ensure no due_datetime provided
            payload.pop("due_datetime", None)
        else:
            payload["due_datetime"] = iso

    if dry_run:
        print(f"[DRY RUN] Would update task {task_id} with:", payload)
        return True

    url = f"{TODOIST_API_BASE}/tasks/{task_id}"
    r = requests.post(url, headers=todoist_headers(api_token), json=payload, timeout=20)
    # Todoist returns 204 No Content on success for update.
    if r.status_code not in (200, 204):
        r.raise_for_status()
    return True

def main():
    parser = argparse.ArgumentParser(description="Sync ICS (iCalendar) to Todoist project.")
    parser.add_argument("--ical-url", required=True, help="URL to .ics calendar")
    parser.add_argument("--todoist-token", default=os.getenv("TODOIST_API_TOKEN"),
                        help="Todoist API token (or set TODOIST_API_TOKEN env var)")
    parser.add_argument("--project-id", required=True, help="Todoist project id (numeric)")
    parser.add_argument("--marker", default=os.getenv("TODOIST_MARKER", DEFAULT_MARKER),
                        help=f"Marker used to tag tasks with UID (default: {DEFAULT_MARKER})")
    parser.add_argument("--dry-run", action="store_true", help="Do not create/update tasks; just show actions")
    parser.add_argument("--update-existing", action="store_true",
                        help="If an existing task for an event exists, update it when properties changed")
    parser.add_argument("--limit", type=int, default=None, help="Max number of events to process (for testing)")

    args = parser.parse_args()

    if not args.todoist_token:
        print("ERROR: Todoist API token required (pass --todoist-token or set TODOIST_API_TOKEN).")
        return

    print("Fetching calendar:", args.ical_url)
    ics = fetch_ics(args.ical_url)
    events = parse_calendar(ics)
    if args.limit:
        events = events[:args.limit]
    print(f"Parsed {len(events)} events from calendar (raw).")

    print("Fetching existing tasks for project", args.project_id)
    tasks = list_tasks_for_project(args.todoist_token, args.project_id)
    existing_by_uid = find_existing_tasks_by_uid(tasks, marker=args.marker)
    print(f"Found {len(existing_by_uid)} existing synced tasks in project (by marker).")

    created = 0
    skipped = 0
    updated = 0

    for ev in events:
        uid = ev["uid"]
        if not uid:
            print("Skipping event with no UID:", ev["summary"])
            skipped += 1
            continue

        existing = existing_by_uid.get(uid)
        if existing:
            # Optionally update if changed
            if args.update_existing:
                # compute minimal equality check: content and due
                new_content_main = ev["summary"] or "Untitled event"
                new_extras = []
                if ev["location"]:
                    new_extras.append(f"@{ev['location']}")
                if ev["description"]:
                    new_extras.append((ev["description"][:120] + ("..." if len(ev["description"])>120 else "")))
                new_marker_text = f" ({args.marker}{uid})"
                new_content = new_content_main
                if new_extras:
                    new_content = f"{new_content} — {' | '.join(new_extras)}"
                new_content = new_content + new_marker_text

                # determine existing due string
                existing_due = existing.get("due", {})
                existing_due_dt = existing_due.get("date") or existing_due.get("datetime")
                # determine new due
                if ev["dtstart"] is not None:
                    new_due = isoformat_for_todoist(ev["dtstart"])
                else:
                    new_due = None

                need_update = False
                if existing.get("content") != new_content:
                    need_update = True
                elif (existing_due_dt is None and new_due is not None) or (existing_due_dt is not None and new_due is None):
                    need_update = True
                elif existing_due_dt and new_due and existing_due_dt != new_due:
                    need_update = True

                if need_update:
                    print("Updating task for event:", ev["summary"], "UID:", uid)
                    try:
                        update_task_for_event(args.todoist_token, existing["id"], ev, marker=args.marker, dry_run=args.dry_run)
                        updated += 1
                    except Exception as e:
                        print("Error updating task:", e)
                else:
                    skipped += 1
            else:
                skipped += 1
            continue

        # create
        print("Creating task for event:", ev["summary"], "UID:", uid)
        try:
            created_task = create_task_for_event(args.todoist_token, args.project_id, ev, marker=args.marker, dry_run=args.dry_run)
            if created_task:
                created += 1
        except Exception as e:
            print("Error creating task:", e)

    print(f"Done. Created: {created}, Updated: {updated}, Skipped: {skipped}")

if __name__ == "__main__":
    main()
