"""GET /tasks/overdue obeys the read visibility rule (SPEC.md sections 1 and 3).

A member may read the tasks they created or are currently assigned, and the overdue view
holds every one of those that is unfinished and due strictly earlier than at, in increasing
ID: the tasks someone else created and assigned to the member included. A task the member
neither created nor is assigned to stays out. An administrator or observer sees every
overdue task. For every actor the view is exactly the unfinished, earlier-due part of what
GET /tasks lists for them.
"""

from datetime import datetime

import pytest


HEADERS = {name: {"Authorization": f"Bearer demo-{name}"} for name in ("admin", "alice", "bob", "observer")}
IDS = {"admin": 1, "alice": 2, "bob": 3, "observer": 4}
AT = "2026-02-01T00:00:00Z"
EARLIER = "2026-01-01T09:00:00Z"
EXACTLY = "2026-02-01T01:00:00+01:00"
LATER = "2026-02-01T00:00:00.000001Z"

# Created in this order, so the IDs a member sees mix tasks they created with tasks assigned
# to them. name: (creator, assignee, due_at, status)
TASKS = {
    "alice-for-bob": ("alice", "bob", EARLIER, "todo"),
    "alice-unassigned": ("alice", None, EARLIER, "todo"),
    "bob-unassigned": ("bob", None, EARLIER, "todo"),
    "admin-for-bob-in-progress": ("admin", "bob", EARLIER, "in_progress"),
    "admin-unassigned": ("admin", None, EARLIER, "todo"),
    "bob-for-alice": ("bob", "alice", EARLIER, "todo"),
    "admin-for-alice": ("admin", "alice", EARLIER, "todo"),
    "alice-for-bob-done": ("alice", "bob", EARLIER, "done"),
    "alice-for-bob-due-exactly": ("alice", "bob", EXACTLY, "todo"),
    "alice-for-bob-due-later": ("alice", "bob", LATER, "todo"),
    "alice-for-bob-undated": ("alice", "bob", None, "todo"),
    "alice-for-admin": ("alice", "admin", EARLIER, "todo"),
    "admin-for-bob": ("admin", "bob", EARLIER, "todo"),
}
OVERDUE = [name for name, (_, _, due_at, status) in TASKS.items() if due_at == EARLIER and status != "done"]


def create(client, creator, assignee, due_at, title="Task"):
    body = {"title": title, "assignee_id": IDS.get(assignee), "due_at": due_at}
    response = client.post("/tasks", json=body, headers=HEADERS[creator])
    assert response.status_code == 201, response.text
    return response.json()


def advance(client, task_id, status):
    for step in {"todo": [], "in_progress": ["in_progress"], "done": ["in_progress", "done"]}[status]:
        response = client.patch(f"/tasks/{task_id}", json={"status": step}, headers=HEADERS["admin"])
        assert response.status_code == 200, response.text


def create_all(client):
    ids = {}
    for name, (creator, assignee, due_at, status) in TASKS.items():
        ids[name] = create(client, creator, assignee, due_at, title=f"Task {name}")["id"]
        advance(client, ids[name], status)
    return ids


def overdue(client, actor, at=AT):
    response = client.get("/tasks/overdue", params={"at": at}, headers=HEADERS[actor])
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"items"}
    return response.json()["items"]


def listed(client, actor):
    response = client.get("/tasks", params={"limit": 100}, headers=HEADERS[actor])
    assert response.status_code == 200, response.text
    return response.json()["items"]


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.utcoffset() is not None, value
    return parsed


def readable(actor, name):
    creator, assignee = TASKS[name][:2]
    return actor in ("admin", "observer") or actor in (creator, assignee)


def test_task_assigned_to_bob_by_alice_is_in_his_overdue_view(client):
    # The issue's own steps (#10).
    task = create(client, "alice", "bob", EARLIER, title="Send the March report")
    assert task["creator_id"] == IDS["alice"] and task["assignee_id"] == IDS["bob"]
    assert [row["id"] for row in listed(client, "bob")] == [task["id"]]
    read = client.get(f"/tasks/{task['id']}", headers=HEADERS["bob"])
    assert (read.status_code, read.json()) == (200, task)
    for actor in ("bob", "alice", "admin", "observer"):
        assert overdue(client, actor) == [task], actor


def test_tasks_bob_created_and_tasks_assigned_to_him_both_appear_in_id_order(client):
    assigned = create(client, "alice", "bob", EARLIER)
    own = create(client, "bob", None, EARLIER)
    assigned_later = create(client, "admin", "bob", EARLIER)
    assert overdue(client, "bob") == [assigned, own, assigned_later]


@pytest.mark.parametrize("actor", sorted(HEADERS))
def test_overdue_view_holds_every_readable_unfinished_task_due_earlier(client, actor):
    ids = create_all(client)
    expected = sorted(ids[name] for name in OVERDUE if readable(actor, name))
    assert [row["id"] for row in overdue(client, actor)] == expected


@pytest.mark.parametrize("actor", sorted(HEADERS))
def test_overdue_view_is_the_overdue_part_of_the_task_list(client, actor):
    # The same rows, field for field, as GET /tasks gives the actor, filtered to unfinished
    # tasks due strictly earlier than at.
    ids = create_all(client)
    rows = listed(client, actor)
    assert [row["id"] for row in rows] == sorted(ids[name] for name in TASKS if readable(actor, name))
    expected = [row for row in rows if row["status"] != "done" and row["due_at"] is not None
                and instant(row["due_at"]) < instant(AT)]
    assert expected and overdue(client, actor) == expected


@pytest.mark.parametrize("actor", ["alice", "bob"])
def test_task_the_member_neither_created_nor_is_assigned_stays_out(client, actor):
    other = "bob" if actor == "alice" else "alice"
    hidden = [create(client, "admin", None, EARLIER)["id"], create(client, other, None, EARLIER)["id"],
              create(client, other, "admin", EARLIER)["id"]]
    assert overdue(client, actor) == []
    assert [row["id"] for row in overdue(client, "admin")] == hidden


def test_overdue_view_follows_the_current_assignment(client):
    task = create(client, "admin", "bob", EARLIER)
    assert [row["id"] for row in overdue(client, "bob")] == [task["id"]]
    assert overdue(client, "alice") == []
    moved = client.patch(f"/tasks/{task['id']}", json={"assignee_id": IDS["alice"]}, headers=HEADERS["admin"])
    assert moved.status_code == 200, moved.text
    assert overdue(client, "bob") == []
    assert overdue(client, "alice") == [moved.json()]


def test_task_assigned_to_the_member_leaves_the_view_when_done_or_not_yet_overdue(client):
    task = create(client, "alice", "bob", EARLIER)
    assert overdue(client, "bob", at=EARLIER) == []
    assert [row["id"] for row in overdue(client, "bob", at="2026-01-01T09:00:00.000001Z")] == [task["id"]]
    advance(client, task["id"], "done")
    assert overdue(client, "bob") == []
