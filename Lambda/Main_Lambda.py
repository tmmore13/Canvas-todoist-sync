import os
import json
import requests
import icalendar
from datetime import datetime

DEFAULT_MARKER = "ICAL-"
# Define the path for the file that will store the state of synced tasks.
# Note: In an AWS Lambda environment, the local filesystem is ephemeral.
# For persistent state across invocations, consider using a service like S3 or DynamoDB.
SYNCED_TASKS_FILE = "synced_tasks.json"

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
    Loads the dictionary of synced tasks (ical_uid: todoist_task_id) from a JSON file.
    Returns an empty dictionary if the file doesn't exist or is invalid.
    """
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Sync state file not found. Starting fresh.")
        return {}
    except json.JSONDecodeError:
        print("Could not decode sync state file. Starting fresh.")
        return {}

def save_synced_tasks(filepath, tasks):
    """Saves the dictionary of synced tasks to a JSON file."""
    with open(filepath, "w") as f:
        json.dump(tasks, f, indent=4)

# ---------------- HELPERS ----------------
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
            "description": str(component.get("DESCRIPTION", "")),
            "location": str(component.get("LOCATION", "")),
            "start": component.get("DTSTART").dt if component.get("DTSTART") else None,
            "end": component.get("DTEND").dt if component.get("DTEND") else None,
        })
    return events

def create_task(api_token, project_id, event, marker):
    """Creates a new task in Todoist."""
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    # The UID is embedded in the content to maintain a link to the original event.
    content = f"{event['summary']} ({marker}{event['uid']})"

    resp = requests.post(
        "https://api.todoist.com/rest/v2/tasks",
        headers=headers,
        data=json.dumps({"content": content, "project_id": project_id}),
    )
    resp.raise_for_status()
    return resp.json()

def delete_task(api_token, task_id):
    """Deletes a task from Todoist."""
    headers = {"Authorization": f"Bearer {api_token}"}
    resp = requests.delete(f"https://api.todoist.com/rest/v2/tasks/{task_id}", headers=headers)
    # A successful deletion returns 204 No Content.
    if resp.status_code != 204:
        raise Exception(f"Failed to delete task {task_id}: {resp.status_code} {resp.text}")
    print(f"Successfully deleted task {task_id}")


# ---------------- SYNC ----------------
def sync_once(cfg):
    """
    Performs a single sync operation using a local JSON file for state management.
    It compares the events from the iCal feed with the tasks stored in the local
    state file, creating or deleting tasks in Todoist as necessary.
    """
    # Load the state of previously synced tasks from the local file
    synced_tasks = load_synced_tasks(SYNCED_TASKS_FILE)
    
    # Fetch current events from iCal feed
    events = fetch_ical_events(cfg["ical_url"])
    event_uids_map = {e["uid"]: e for e in events}

    # Determine which tasks to create and which to delete by comparing sets of UIDs
    current_event_uids = set(event_uids_map.keys())
    locally_synced_uids = set(synced_tasks.keys())
    
    uids_to_create = current_event_uids - locally_synced_uids
    uids_to_delete = locally_synced_uids - current_event_uids

    created_count, deleted_count = 0, 0

    # Create tasks for new events
    for uid in uids_to_create:
        event = event_uids_map[uid]
        print(f"Creating task for new event: {event['summary']} (UID: {uid})")
        try:
            new_task = create_task(cfg["todoist_api_token"], cfg["todoist_project_id"], event, cfg["marker"])
            # Add the newly created task to our local state mapping UID to Todoist task ID
            synced_tasks[uid] = new_task["id"]
            created_count += 1
        except Exception as e:
            print(f"ERROR: Failed to create task for event {uid}: {e}")

    # Delete tasks for events that no longer exist
    for uid in uids_to_delete:
        task_id = synced_tasks[uid]
        print(f"Deleting task {task_id} for removed event (UID: {uid})")
        try:
            delete_task(cfg["todoist_api_token"], task_id)
            # Remove the deleted task from our local state
            del synced_tasks[uid]
            deleted_count += 1
        except Exception as e:
            print(f"ERROR: Failed to delete task {task_id}: {e}")
    
    # Persist the updated state to the file after all operations
    save_synced_tasks(SYNCED_TASKS_FILE, synced_tasks)
    
    print(f"Sync complete. Created: {created_count}, Deleted: {deleted_count}.")
    return {"created": created_count, "deleted": deleted_count}

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
