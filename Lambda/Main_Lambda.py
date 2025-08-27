import os
import json
import requests
import icalendar
from datetime import datetime

DEFAULT_MARKER = "ICAL-"

# ---------------- CONFIG ----------------
def load_config():
    return {
        "todoist_api_token": os.environ["TODOIST_API_TOKEN"],
        "todoist_project_id": os.environ["TODOIST_PROJECT_ID"],
        "ical_url": os.environ["ICAL_URL"],
        "marker": os.getenv("MARKER", DEFAULT_MARKER),
    }

# ---------------- HELPERS ----------------
def fetch_ical_events(ical_url):
    resp = requests.get(ical_url)
    resp.raise_for_status()
    cal = icalendar.Calendar.from_ical(resp.content)

    events = []
    for component in cal.walk("VEVENT"):
        events.append({
            "uid": str(component.get("UID")),
            "summary": str(component.get("SUMMARY", "")),
            "description": str(component.get("DESCRIPTION", "")),
            "location": str(component.get("LOCATION", "")),
            "start": component.get("DTSTART").dt if component.get("DTSTART") else None,
            "end": component.get("DTEND").dt if component.get("DTEND") else None,
        })
    return events

def fetch_todoist_tasks(api_token, project_id, marker):
    headers = {"Authorization": f"Bearer {api_token}"}
    resp = requests.get("https://api.todoist.com/rest/v2/tasks", headers=headers, params={"project_id": project_id})
    resp.raise_for_status()
    tasks = resp.json()

    # only tasks created by this sync (marked)
    return {t["content"]: t for t in tasks if marker in t["content"]}

def create_task(api_token, project_id, event, marker):
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    content = f"{event['summary']} ({marker}{event['uid']})"

    resp = requests.post(
        "https://api.todoist.com/rest/v2/tasks",
        headers=headers,
        data=json.dumps({"content": content, "project_id": project_id}),
    )
    resp.raise_for_status()
    return resp.json()

def delete_task(api_token, task_id):
    headers = {"Authorization": f"Bearer {api_token}"}
    resp = requests.delete(f"https://api.todoist.com/rest/v2/tasks/{task_id}", headers=headers)
    if resp.status_code not in (200, 204):
        raise Exception(f"Failed to delete task {task_id}: {resp.text}")

# ---------------- SYNC ----------------
def sync_once(cfg):
    events = fetch_ical_events(cfg["ical_url"])
    todoist_tasks = fetch_todoist_tasks(cfg["todoist_api_token"], cfg["todoist_project_id"], cfg["marker"])

    # map UIDs from events
    event_uids = {e["uid"]: e for e in events}
    synced_uids = {c.split(cfg["marker"])[-1].rstrip(")") for c in todoist_tasks}

    created, deleted = [], []

    # Create missing events
    for uid, event in event_uids.items():
        if uid not in synced_uids:
            created.append(create_task(cfg["todoist_api_token"], cfg["todoist_project_id"], event, cfg["marker"]))

    # Delete tasks whose events are gone
    for content, task in todoist_tasks.items():
        uid = content.split(cfg["marker"])[-1].rstrip(")")
        if uid not in event_uids:
            delete_task(cfg["todoist_api_token"], task["id"])
            deleted.append(task["id"])

    return {"created": len(created), "deleted": len(deleted)}

# ---------------- LAMBDA HANDLER ----------------
def lambda_handler(event, context):
    try:
        cfg = load_config()
        result = sync_once(cfg)
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "ok", "result": result}),
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"status": "error", "message": str(e)}),
        }
