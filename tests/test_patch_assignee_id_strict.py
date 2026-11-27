"""PATCH takes assignee_id only as a JSON integer or null, as POST does (SPEC.md sections 2 and 5).

`assignee_id` is null or an existing admin/member ID, and booleans and floats are not
integers for input validation. A boolean, a numeric string or a float is invalid input, 422,
on update as on creation, even when it would convert to an existing user's ID (true to the
Administrator, "3" and 3.0 to Bob). The refused request changes nothing: the task reads back
as it was and no status-history event is written, even when the body also carries valid
fields and a status transition. The integer itself, and null, are still accepted.
"""

import pytest
from fastapi.testclient import TestClient

from workflow.api import create_app


HEADERS = {name: {"Authorization": f"Bearer demo-{name}"} for name in ("admin", "alice")}
# Each value with the existing user ID a lax integer conversion would make of it; false
# would make 0, which names no user.
NOT_INTEGERS = {
    "true": (True, 1),
    "false": (False, None),
    "string-1": ("1", 1),
    "string-2": ("2", 2),
    "string-3": ("3", 3),
    "float-1.0": (1.0, 1),
    "float-2.0": (2.0, 2),
    "float-3.0": (3.0, 3),
}
EXTRAS = {"alone": {}, "with-valid-fields": {"title": "Renamed", "priority": 5, "status": "in_progress"}}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "assignee.sqlite3")) as result:
        yield result


@pytest.fixture
def task(client):
    """Alice's todo task, assigned to her."""
    body = {"title": "Plan the review", "assignee_id": 2}
    response = client.post("/tasks", json=body, headers=HEADERS["alice"])
    assert response.status_code == 201, response.text
    return response.json()


def state(client, task_id):
    """The task and its status history as the administrator reads them."""
    task = client.get(f"/tasks/{task_id}", headers=HEADERS["admin"])
    history = client.get(f"/tasks/{task_id}/history", headers=HEADERS["admin"])
    assert (task.status_code, history.status_code) == (200, 200), (task.text, history.text)
    return task.json(), history.json()


@pytest.mark.parametrize("value, converted", list(NOT_INTEGERS.values()), ids=list(NOT_INTEGERS))
@pytest.mark.parametrize("extra", list(EXTRAS.values()), ids=list(EXTRAS))
@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_patch_with_assignee_id_not_an_integer_is_refused_and_changes_nothing(client, task, actor, extra,
                                                                              value, converted):
    before = state(client, task["id"])
    assert before[0] == task
    response = client.patch(f"/tasks/{task['id']}", json={**extra, "assignee_id": value}, headers=HEADERS[actor])
    assert response.status_code == 422, response.text
    assert state(client, task["id"]) == before
    if converted is not None:
        # The same body with the integer itself is accepted: the type alone was refused.
        accepted = client.patch(f"/tasks/{task['id']}", json={**extra, "assignee_id": converted},
                                headers=HEADERS[actor])
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["assignee_id"] == converted


@pytest.mark.parametrize("assignee_id", [1, 3, None], ids=["admin", "member", "null"])
@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_patch_with_assignee_id_integer_or_null_is_accepted(client, task, actor, assignee_id):
    history = state(client, task["id"])[1]
    response = client.patch(f"/tasks/{task['id']}", json={"assignee_id": assignee_id}, headers=HEADERS[actor])
    assert response.status_code == 200, response.text
    assert state(client, task["id"]) == (response.json(), history)
    # Only the assignee and the update time change.
    assert {**response.json(), "updated_at": task["updated_at"]} == {**task, "assignee_id": assignee_id}


@pytest.mark.parametrize("assignee_id", [4, 99], ids=["observer", "missing"])
def test_patch_with_assignee_id_of_no_admin_or_member_is_refused(client, task, assignee_id):
    before = state(client, task["id"])
    response = client.patch(f"/tasks/{task['id']}", json={"assignee_id": assignee_id}, headers=HEADERS["alice"])
    assert response.status_code == 422, response.text
    assert state(client, task["id"]) == before


@pytest.mark.parametrize("value, converted", list(NOT_INTEGERS.values()), ids=list(NOT_INTEGERS))
def test_create_with_assignee_id_not_an_integer_is_refused(client, task, value, converted):
    response = client.post("/tasks", json={"title": "Plan the review", "assignee_id": value},
                           headers=HEADERS["alice"])
    assert response.status_code == 422, response.text
    listed = client.get("/tasks", headers=HEADERS["admin"]).json()
    assert ([row["id"] for row in listed["items"]], listed["total"]) == ([task["id"]], 1)
