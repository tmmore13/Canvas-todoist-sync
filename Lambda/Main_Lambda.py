#!/usr/bin/env python3
"""
Sync an iCalendar (ICS) feed to a Todoist project, using a config file.

Features:
- Creates tasks for new events
- Updates tasks when events change (if update_existing: true)
- Deletes tasks when events are removed from the calendar

Config:
    Provide config.json or config.yaml with keys:
    {
      "ical_url": "...",
      "todoist_token": "...",
      "project_id": "...",
      "update_existing": true,
      "dry_run": false
    }
"""

import os
import re
import json
import yaml   # pip install pyyaml
import requests
from datetime import datetime, date
from dateutil import tz
from icalendar import Calendar

TODOIST_API_BASE = "https://api.todoist.com/rest/v2"
DEFAULT_MARKER = "ICUID:"

# -------------------------
# Helpers
# -------------------------

def load_config(path="config.json"):
    """Load JSON or YAML config file."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    if path.endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    if path.endswith((".yml", ".yaml")):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    raise ValueError("Config file must be .json or .yaml")

def isoformat_for_todoist(dt):
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt.isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz.tzutc())
    return dt.isoformat()

def fetch_ics(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.content

def parse_ics(data):
    cal = Calendar.from_ical(data)
    events = []
    for comp in cal.walk("VEVENT"):
        events.append({
            "uid": str(comp.get("uid", "")),
            "summary": str(comp.get("summary", "Untitled event")),
            "description": str(comp.get("description", "") or ""),
            "location": str(comp.get("location", "") or ""),
            "dtstart": comp.get("dtstart").dt if comp.get("dtstart") else None,
        })
    return events

def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def list_tasks(token, project_id):
    r = requests.get(f"{TODOIST_API_BASE}/tasks", headers=headers(token), params={"project_id": project_id})
    r.raise_for_status()
    return r.json()

def find_existing(tasks, marker=DEFAULT_MARKER):
    mapping = {}
    regex = re.compile(rf"{re.escape(marker)}([^\s)]+)")
    for t in tasks:
        m = regex.search(t.get("content", ""))
        if m:
            mapping[m.group(1)] = t
    return mapping

def build_content(event, marker):
    extras = []
    if event["location"]:
        extras.append(f"@{event['location']}")
    if event["description"]:
        d = event["description"]
        extras.append(d[:120] + ("..." if len(d) > 120 else ""))
    content = event["summary"]
    if extras:
        content += " — " + " | ".join(extras)
    return f"{content} ({marker}{event['uid']})"

def create_task(token, project_id, event, marker, dry_run=False):
    payload = {"content": build_content(event, marker), "project_id": project_id}
    if event["dtstart"]:
        iso = isoformat_for_todoist(event["dtstart"])
        if isinstance(event["dtstart"], date) and not isinstance(event["dtstart"], datetime):
            payload["due_date"] = iso
        else:
            payload["due_datetime"] = iso
    if dry_run:
        print("[DRY RUN] Would create:", payload)
        return
    r = requests.post(f"{TODOIST_API_BASE}/tasks", headers=headers(token), json=payload)
    r.raise_for_status()
    return r.json()

def update_task(token, task_id, event, marker, dry_run=False):
    payload = {"content": build_content(event, marker)}
    if event["dtstart"]:
        iso = isoformat_for_todoist(event["dtstart"])
        if isinstance(event["dtstart"], date) and not isinstance(event["dtstart"], datetime):
            payload["due_date"] = iso
        else:
            payload["due_datetime"] = iso
    if dry_run:
        print(f"[DRY RUN] Would update {task_id}:", payload)
        return
    r = requests.post(f"{TODOIST_API_BASE}/tasks/{task_id}", headers=headers(token), json=payload)
    if r.status_code not in (200, 204):
        r.raise_for_status()

def delete_task(token, task_id, dry_run=False):
    if dry_run:
        print(f"[DRY RUN] Would delete task {task_id}")
        return
    r = requests.delete(f"{TODOIST_API_BASE}/tasks/{task_id}", headers=headers(token))
    if r.status_code not in (200, 204):
        r.raise_for_status()

# -------------------------
# Main
# -------------------------

def main():
    cfg = load_config("config.json")  # change to config.yaml if needed

    ical_url = cfg["ical_url"]
    token = cfg["todoist_token"]
    project_id = cfg["project_id"]
    update_existing = cfg.get("update_existing", False)
    dry_run = cfg.get("dry_run", False)

    events = parse_ics(fetch_ics(ical_url))
    tasks = list_tasks(token, project_id)
    existing = find_existing(tasks)

    ical_uids = {ev["uid"] for ev in events if ev["uid"]}
    created, updated, skipped, deleted = 0, 0, 0, 0

    for ev in events:
        uid = ev["uid"]
        if not uid:
            skipped += 1
            continue
        if uid in existing:
            if update_existing:
                update_task(token, existing[uid]["id"], ev, DEFAULT_MARKER, dry_run)
                updated += 1
            else:
                skipped += 1
        else:
            create_task(token, project_id, ev, DEFAULT_MARKER, dry_run)
            created += 1

    for uid, task in existing.items():
        if uid not in ical_uids:
            print(f"Deleting task {task['id']} (event UID {uid} missing from calendar)")
            delete_task(token, task["id"], dry_run)
            deleted += 1

    print(f"Done. Created={created}, Updated={updated}, Deleted={deleted}, Skipped={skipped}")

if __name__ == "__main__":
    main()
