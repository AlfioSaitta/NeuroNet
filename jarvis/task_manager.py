import os
import json
import logging
from uuid import uuid4

logger = logging.getLogger("chameleon.tasks")
TASKS_FILE = os.path.join(os.path.dirname(__file__), "tasks.json")

def load_tasks():
    if os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading tasks: {e}")
    return {}

def save_tasks(tasks):
    try:
        import os
        tmp = TASKS_FILE + ".tmp"
        with open(tmp, 'w') as f:
            json.dump(tasks, f, indent=4)
        os.replace(tmp, TASKS_FILE)
    except Exception as e:
        logger.error(f"Error saving tasks: {e}")

def add_todo(desc, priority="media", deadline="nessuna", task_type="personale", user_id=None):
    tasks = load_tasks()
    tid = uuid4().hex[:6]
    owner = "global" if task_type == "progetto" else str(user_id)
    tasks[tid] = {
        "desc": desc,
        "priority": priority,
        "deadline": deadline,
        "status": "pending",
        "owner": owner
    }
    save_tasks(tasks)
    return tid

def mark_done(tid, user_id=None):
    tasks = load_tasks()
    if tid in tasks:
        owner = tasks[tid].get("owner", "global")
        if owner == "global" or owner == str(user_id):
            tasks[tid]["status"] = "completed"
            save_tasks(tasks)
            return True
    return False

def remove_todo(tid, user_id=None):
    tasks = load_tasks()
    if tid in tasks:
        owner = tasks[tid].get("owner", "global")
        if owner == "global" or owner == str(user_id):
            del tasks[tid]
            save_tasks(tasks)
            return True
    return False

def get_open_tasks(user_id=None):
    tasks = load_tasks()
    open_t = {}
    for k, v in tasks.items():
        if v["status"] == "pending":
            if user_id is None:
                open_t[k] = v
            else:
                owner = v.get("owner", "global")
                if owner == "global" or owner == str(user_id):
                    open_t[k] = v
    return open_t
