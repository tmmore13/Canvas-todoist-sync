import os
import json
import requests
import icalendar
from datetime import datetime, date, timezone

DEFAULT_MARKER = "ICAL-"
# In AWS Lambda, only the /tmp directory is writable.
SYNCED_TASKS_FILE = "/tmp/synced_tasks.json"

# ---------------- CONFIG ----------------
def load_config():
    """Loads configuration from environment variables."""
    return {
        "todoist_api_token": os.environ["TODOIST_API_TOKEN"],
        "todoist_project_id": os.environ["TODOIST_PROJECT_ID"],
        "ical_url": os.environ["ICAL_URL"],
        "marker": os.getenv("MARKER", DEFAULT_MARKER),
    }

# ---------------- STATE MANAGEMENT ----------------
def load_synced_tasks(filepath):
    """
    Loads the dictionary of synced tasks from a JSON file.
    The structure is { "ical_uid": {"task_id": "...", "due": "..."} }
    """
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("Sync state file not found or invalid. Starting fresh.")
        return {}

def save_synced_tasks(filepath, tasks):
    """Saves the dictionary of synced tasks to a JSON file."""
    with open(filepath, "w") as f:
        json.dump(tasks, f, indent=4)

# ---------------- HELPERS ----------------
def get_due_string(dt_obj):
    """Converts a datetime or date object to a standardized ISO string for comparison."""
    if isinstance(dt_obj, datetime):
        # Convert timezone-aware datetimes to UTC, assume naive are UTC
        dt_utc = dt_obj.astimezone(timezone.utc) if dt_obj.tzinfo else dt_obj.replace(tzinfo=timezone.utc)
        return dt_utc.isoformat()
    elif isinstance(dt_obj, date):
        return dt_obj.isoformat()
    return None

def format_due_payload(dt_obj):
    """Formats a date/datetime object into the correct dictionary structure for the Todoist API."""
    if isinstance(dt_obj, datetime):
        # Convert to UTC and format for Todoist's due_datetime field
        dt_utc = dt_obj.astimezone(timezone.utc) if dt_obj.tzinfo else dt_obj.replace(tzinfo=timezone.utc)
        return {"due_datetime": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")}
    elif isinstance(dt_obj, date):
        # Format for Todoist's due_date field (for all-day events)
        return {"due_date": dt_obj.strftime("%Y-%m-%d")}
    return {}

def fetch_ical_events(ical_url):
    """Fetches and parses events from an iCal URL."""
    resp = requests.get(ical_url)
    resp.raise_for_status()
    cal = icalendar.Calendar.from_ical(resp.content)
    events = []
    for component in cal.walk("VEVENT"):
        events.append({
            "uid": str(component.get("UID")),
            "summary": str(component.get("SUMMARY", "")),
            "start": component.get("DTSTART").dt if component.get("DTSTART") else None,
        })
    return events

# ---------------- API CALLS ----------------
def create_task(api_token, project_id, event, marker):
    """Creates a new task in Todoist, including the due date."""
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    content = f"{event['summary']} ({marker}{event['uid']})"
    payload = {"content": content, "project_id": project_id}
    payload.update(format_due_payload(event.get("start")))

    resp = requests.post(
        "https://api.todoist.com/rest/v2/tasks",
        headers=headers,
        data=json.dumps(payload),
    )
    resp.raise_for_status()
    return resp.json()

def update_task_due_date(api_token, task_id, event):
    """Updates the due date of an existing task in Todoist."""
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    payload = format_due_payload(event.get("start"))

    if not payload:
        return # Nothing to update
    
    resp = requests.post(
        f"https://api.todoist.com/rest/v2/tasks/{task_id}",
        headers=headers,
        data=json.dumps(payload),
    )
    if resp.status_code not in (200, 204):
        raise Exception(f"Failed to update task {task_id}: {resp.status_code} {resp.text}")
    print(f"Successfully updated due date for task {task_id}")

def delete_task(api_token, task_id):
    """Deletes a task from Todoist."""
    headers = {"Authorization": f"Bearer {api_token}"}
    resp = requests.delete(f"https://api.todoist.com/rest/v2/tasks/{task_id}", headers=headers)
    if resp.status_code != 204:
        raise Exception(f"Failed to delete task {task_id}: {resp.status_code} {resp.text}")
    print(f"Successfully deleted task {task_id}")

# ---------------- SYNC ----------------
def sync_once(cfg):
    """Performs a single sync operation, handling task creation, updates, and deletion."""
    synced_tasks = load_synced_tasks(SYNCED_TASKS_FILE)
    events = fetch_ical_events(cfg["ical_url"])
    event_uids_map = {e["uid"]: e for e in events}
    
    created_count, updated_count, deleted_count = 0, 0, 0
    seen_uids = set()

    # Process current events: Create or Update
    for uid, event in event_uids_map.items():
        seen_uids.add(uid)
        event_due_str = get_due_string(event.get("start"))

        if uid not in synced_tasks:
            print(f"Creating task for new event: {event['summary']} (UID: {uid})")
            try:
                new_task = create_task(cfg["todoist_api_token"], cfg["todoist_project_id"], event, cfg["marker"])
                synced_tasks[uid] = {"task_id": new_task["id"], "due": event_due_str}
                created_count += 1
            except Exception as e:
                print(f"ERROR: Failed to create task for event {uid}: {e}")
        else:
            if synced_tasks[uid].get("due") != event_due_str:
                task_id = synced_tasks[uid]["task_id"]
                print(f"Updating due date for task {task_id} (UID: {uid})")
                try:
                    update_task_due_date(cfg["todoist_api_token"], task_id, event)
                    synced_tasks[uid]["due"] = event_due_str
                    updated_count += 1
                except Exception as e:
                    print(f"ERROR: Failed to update task {task_id}: {e}")

    # Process deleted events
    uids_to_delete = set(synced_tasks.keys()) - seen_uids
    for uid in list(uids_to_delete):
        task_id = synced_tasks[uid]["task_id"]
        print(f"Deleting task {task_id} for removed event (UID: {uid})")
        try:
            delete_task(cfg["todoist_api_token"], task_id)
            del synced_tasks[uid]
            deleted_count += 1
        except Exception as e:
            print(f"ERROR: Failed to delete task {task_id}: {e}")
    
    save_synced_tasks(SYNCED_TASKS_FILE, synced_tasks)
    
    result = {"created": created_count, "updated": updated_count, "deleted": deleted_count}
    print(f"Sync complete. Result: {result}")
    return result

# ---------------- LAMBDA HANDLER ----------------
def lambda_handler(event, context):
    """AWS Lambda handler function."""
    try:
        print("Starting sync process...")
        cfg = load_config()
        result = sync_once(cfg)
        print("Sync process finished successfully.")
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "ok", "result": result}),
        }
    except Exception as e:
        print(f"An error occurred during sync: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"status": "error", "message": str(e)}),
        }


