"""GET /tasks/overdue holds every overdue task, however many the caller can see (SPEC.md section 3).

The view is every visible, unfinished task whose due_at is strictly earlier than at, in
increasing ID. It is not a page: GET /tasks pages its answer by at most 100, the overdue view
has no limit, so an overdue task after the caller's first 100 visible tasks is in it, and more
than 100 overdue tasks are all in it. For every actor the view is exactly the unfinished,
earlier-due part of everything GET /tasks lists for them, page after page.
"""

from datetime import datetime

import pytest


HEADERS = {name: {"Authorization": f"Bearer demo-{name}"} for name in ("admin", "alice", "bob", "observer")}
IDS = {"admin": 1, "alice": 2, "bob": 3}
AT = "2026-02-01T00:00:00Z"
EARLIER = "2026-01-01T09:00:00Z"
EXACTLY = "2026-02-01T01:00:00+01:00"
LATER = "2026-02-01T00:00:00.000001Z"


def create(client, creator, title, due_at=None, assignee=None):
    body = {"title": title, "due_at": due_at, "assignee_id": IDS.get(assignee)}
    response = client.post("/tasks", json=body, headers=HEADERS[creator])
    assert response.status_code == 201, response.text
    return response.json()


def fillers(client, count, creator="alice"):
    """count tasks without a due_at, as the issue creates them: never overdue."""
    return [create(client, creator, f"Filler {number}")["id"] for number in range(1, count + 1)]


def finish(client, task_id):
    for status in ("in_progress", "done"):
        response = client.patch(f"/tasks/{task_id}", json={"status": status}, headers=HEADERS["admin"])
        assert response.status_code == 200, response.text


def overdue(client, actor, at=AT):
    response = client.get("/tasks/overdue", params={"at": at}, headers=HEADERS[actor])
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"items"}
    return response.json()["items"]


def listed(client, actor):
    """Every task GET /tasks lists for the actor, read page after page of 100."""
    rows, total = [], None
    while total is None or len(rows) < total:
        response = client.get("/tasks", params={"limit": 100, "offset": len(rows)}, headers=HEADERS[actor])
        assert response.status_code == 200, response.text
        page, total = response.json()["items"], response.json()["total"]
        assert page, (len(rows), total)
        rows += page
    return rows


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.utcoffset() is not None, value
    return parsed


def overdue_part(rows, at=AT):
    return [row for row in rows if row["status"] != "done" and row["due_at"] is not None
            and instant(row["due_at"]) < instant(at)]


def test_the_issues_task_after_100_fillers_is_overdue(client):
    # The issue's own steps (#19): the report gets the ID 101.
    assert fillers(client, 100) == list(range(1, 101))
    task = create(client, "alice", "Send the March report", due_at=EARLIER)
    assert task["id"] == 101
    for actor in ("alice", "admin", "observer"):
        assert overdue(client, actor) == [task], actor


def test_the_issues_task_after_30_fillers_is_overdue(client):
    # The issue's comparison: with fewer tasks before it the view held the task already.
    fillers(client, 30)
    task = create(client, "alice", "Send the March report", due_at=EARLIER)
    assert task["id"] == 31
    for actor in ("alice", "admin", "observer"):
        assert overdue(client, actor) == [task], actor


@pytest.mark.parametrize("before", [0, 99, 100, 101, 199, 200])
def test_overdue_task_after_any_number_of_visible_tasks_is_in_the_view(client, before):
    # 99 puts the task 100th, the last one the first 100 hold; 100 puts it 101st.
    fillers(client, before)
    task = create(client, "alice", "Send the March report", due_at=EARLIER)
    assert task["id"] == before + 1
    assert len(listed(client, "alice")) == before + 1
    for actor in ("alice", "admin"):
        assert overdue(client, actor) == [task], actor


def test_more_than_100_overdue_tasks_are_all_in_the_view_in_increasing_id(client):
    due = [create(client, "alice", f"Overdue {number}", due_at=EARLIER)["id"] for number in range(1, 151)]
    assert due == list(range(1, 151))
    for actor in ("alice", "admin", "observer"):
        assert [row["id"] for row in overdue(client, actor)] == due, actor


def test_after_100_tasks_only_unfinished_tasks_due_strictly_earlier_are_in_the_view(client):
    fillers(client, 100)
    created = {
        "earlier": create(client, "alice", "Due earlier", due_at=EARLIER),
        "done": create(client, "alice", "Done, due earlier", due_at=EARLIER),
        "exactly": create(client, "alice", "Due exactly at at", due_at=EXACTLY),
        "later": create(client, "alice", "Due a microsecond later", due_at=LATER),
        "undated": create(client, "alice", "Without a due_at"),
        "in-progress": create(client, "alice", "In progress, due earlier", due_at=EARLIER),
    }
    finish(client, created["done"]["id"])
    started = client.patch(f"/tasks/{created['in-progress']['id']}", json={"status": "in_progress"},
                           headers=HEADERS["admin"])
    assert started.status_code == 200, started.text
    fillers(client, 100)
    created["after-200"] = create(client, "alice", "Due earlier, after 100 more fillers", due_at=EARLIER)
    assert created["earlier"]["id"] == 101 and created["after-200"]["id"] == 207
    expected = [created[name]["id"] for name in ("earlier", "in-progress", "after-200")]
    for actor in ("alice", "admin", "observer"):
        assert [row["id"] for row in overdue(client, actor)] == expected, actor
    # A microsecond later the task due exactly at the old at is overdue too.
    later = [row["id"] for row in overdue(client, "alice", at="2026-02-01T00:00:00.000001Z")]
    assert later == sorted(expected + [created["exactly"]["id"]])


# Created in this order around 120 of Alice's fillers, so Alice sees more than 100 tasks and
# her overdue ones lie before and after her 100th; Bob sees five, three of them with IDs above
# 100. Fillers are a count of Alice's tasks without a due_at.
# name: (creator, assignee, due_at, finished)
AROUND_FILLERS = [
    ("bob-own", ("bob", None, EARLIER, False)),
    ("alice-for-bob", ("alice", "bob", EARLIER, False)),
    (60, None),
    ("admin-for-alice", ("admin", "alice", EARLIER, False)),
    ("admin-own", ("admin", None, EARLIER, False)),
    (60, None),
    ("alice-own", ("alice", None, EARLIER, False)),
    ("bob-for-alice", ("bob", "alice", EARLIER, False)),
    ("bob-own-late", ("bob", None, EARLIER, False)),
    ("alice-own-done", ("alice", None, EARLIER, True)),
    ("alice-own-due-exactly", ("alice", None, EXACTLY, False)),
    ("admin-for-bob", ("admin", "bob", EARLIER, False)),
]


def readable(actor, creator, assignee):
    return actor in ("admin", "observer") or actor in (creator, assignee)


@pytest.mark.parametrize("actor", sorted(HEADERS))
def test_overdue_view_is_the_overdue_part_of_every_listed_task(client, actor):
    # The same rows, field for field, as GET /tasks gives the actor over all its pages,
    # filtered to unfinished tasks due strictly earlier than at.
    ids, visible, due = {}, [], []
    for name, task in AROUND_FILLERS:
        if task is None:
            made = fillers(client, name)
            visible += made if readable(actor, "alice", None) else []
            continue
        creator, assignee, due_at, finished = task
        ids[name] = create(client, creator, f"Task {name}", due_at=due_at, assignee=assignee)["id"]
        if finished:
            finish(client, ids[name])
        if readable(actor, creator, assignee):
            visible.append(ids[name])
            if due_at == EARLIER and not finished:
                due.append(ids[name])
    assert ids["admin-for-bob"] == 130
    rows = listed(client, actor)
    assert [row["id"] for row in rows] == visible
    expected = overdue_part(rows)
    assert [row["id"] for row in expected] == due
    assert overdue(client, actor) == expected
