"""assignee_id 0 is refused as invalid input, 422, as 99 is (SPEC.md sections 2 and 5).

`assignee_id` is null or an existing admin/member ID. No user has the ID 0, so POST /tasks
and PATCH /tasks/{id} with "assignee_id": 0 answer 422 with the detail any other ID of no
administrator or member gets, never an internal SQL error. The refused request changes
nothing: no task is created and no idempotency key is recorded, the patched task and its
status history read back as they were, also after a restart, even when the body also carries
valid fields and a status transition. The administrator's and the members' IDs (1, 2 and 3)
and null are still accepted.
"""

import pytest
from fastapi.testclient import TestClient

from workflow.api import create_app


HEADERS = {name: {"Authorization": f"Bearer demo-{name}"} for name in ("admin", "alice")}
REFUSED = {"detail": "assignee must be an administrator or member"}
# 0 is the one integer the check skipped; the others are refused before and after the fix.
NOT_ASSIGNABLE = {
    "zero": 0,
    "negative": -1,
    "observer": 4,
    "unused-5": 5,
    "unused-99": 99,
    "largest-sqlite-integer": 2**63 - 1,
    "beyond-sqlite-integers": 2**63,
}
ASSIGNABLE = {"admin": 1, "alice": 2, "bob": 3, "null": None}
CREATE_EXTRAS = {"alone": {}, "with-valid-fields": {"description": "Quarterly", "priority": 5,
                                                    "due_at": "2026-12-01T09:00:00+02:00"}}
PATCH_EXTRAS = {"alone": {}, "with-valid-fields": {"title": "Renamed", "priority": 5, "status": "in_progress"}}


@pytest.fixture
def path(tmp_path):
    return tmp_path / "assignee-zero.sqlite3"


@pytest.fixture
def client(path):
    # A server error comes back as its 500 answer, as an HTTP client sees it, not as the
    # exception behind it.
    with TestClient(create_app(path), raise_server_exceptions=False) as result:
        yield result


@pytest.fixture
def task(client):
    """Alice's todo task, assigned to her, as the issue creates it."""
    response = client.post("/tasks", json={"title": "Plan the review", "assignee_id": 2}, headers=HEADERS["alice"])
    assert response.status_code == 201, response.text
    return response.json()


def state(client, task_id):
    """The task and its status history as the administrator reads them."""
    task = client.get(f"/tasks/{task_id}", headers=HEADERS["admin"])
    history = client.get(f"/tasks/{task_id}/history", headers=HEADERS["admin"])
    assert (task.status_code, history.status_code) == (200, 200), (task.text, history.text)
    return task.json(), history.json()


def listing(client):
    """Every task's ID, and the total, as GET /tasks lists them for the administrator."""
    response = client.get("/tasks", headers=HEADERS["admin"])
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["items"]], response.json()["total"]


@pytest.mark.parametrize("value", list(NOT_ASSIGNABLE.values()), ids=list(NOT_ASSIGNABLE))
@pytest.mark.parametrize("extra", list(CREATE_EXTRAS.values()), ids=list(CREATE_EXTRAS))
@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_create_with_assignee_id_of_no_admin_or_member_is_refused_and_creates_nothing(client, task, actor,
                                                                                      extra, value):
    before = listing(client), state(client, task["id"])
    assert before[0] == ([task["id"]], 1)
    body = {"title": "Plan the budget", **extra}
    response = client.post("/tasks", json={**body, "assignee_id": value}, headers=HEADERS[actor])
    assert response.status_code == 422, response.text
    assert response.json() == REFUSED
    assert (listing(client), state(client, task["id"])) == before
    # The same body with a member's ID is accepted: the assignee alone was refused.
    accepted = client.post("/tasks", json={**body, "assignee_id": 3}, headers=HEADERS[actor])
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["assignee_id"] == 3


@pytest.mark.parametrize("value", list(NOT_ASSIGNABLE.values()), ids=list(NOT_ASSIGNABLE))
@pytest.mark.parametrize("extra", list(PATCH_EXTRAS.values()), ids=list(PATCH_EXTRAS))
@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_patch_with_assignee_id_of_no_admin_or_member_is_refused_and_changes_nothing(client, task, actor,
                                                                                     extra, value):
    before = state(client, task["id"])
    assert before[0] == task
    response = client.patch(f"/tasks/{task['id']}", json={**extra, "assignee_id": value}, headers=HEADERS[actor])
    assert response.status_code == 422, response.text
    assert response.json() == REFUSED
    assert state(client, task["id"]) == before
    assert listing(client) == ([task["id"]], 1)
    # The same body with a member's ID is accepted: the assignee alone was refused.
    accepted = client.patch(f"/tasks/{task['id']}", json={**extra, "assignee_id": 3}, headers=HEADERS[actor])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["assignee_id"] == 3


@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_create_refused_for_assignee_id_zero_records_no_idempotency_key(client, task, actor):
    headers = {**HEADERS[actor], "Idempotency-Key": "plan-the-budget"}
    refused = client.post("/tasks", json={"title": "Plan the budget", "assignee_id": 0}, headers=headers)
    assert refused.status_code == 422, refused.text
    assert refused.json() == REFUSED
    assert listing(client) == ([task["id"]], 1)
    # A recorded key would make another body with it 409; the refusal recorded none.
    created = client.post("/tasks", json={"title": "Plan the budget", "assignee_id": 3}, headers=headers)
    assert created.status_code == 201, created.text
    retried = client.post("/tasks", json={"title": "Plan the budget", "assignee_id": 3}, headers=headers)
    assert (retried.status_code, retried.json()["id"]) == (201, created.json()["id"])
    assert listing(client) == ([task["id"], created.json()["id"]], 2)


def test_requests_refused_for_assignee_id_zero_leave_the_file_as_it_was(path, client, task):
    before = listing(client), state(client, task["id"])
    created = client.post("/tasks", json={"title": "Plan the budget", "assignee_id": 0}, headers=HEADERS["alice"])
    patched = client.patch(f"/tasks/{task['id']}", json={"assignee_id": 0, "status": "in_progress"},
                           headers=HEADERS["alice"])
    assert (created.status_code, patched.status_code) == (422, 422), (created.text, patched.text)
    with TestClient(create_app(path)) as restarted:
        assert (listing(restarted), state(restarted, task["id"])) == before


@pytest.mark.parametrize("value", list(ASSIGNABLE.values()), ids=list(ASSIGNABLE))
@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_create_with_assignee_id_of_admin_member_or_null_is_accepted(client, actor, value):
    response = client.post("/tasks", json={"title": "Plan the budget", "assignee_id": value}, headers=HEADERS[actor])
    assert response.status_code == 201, response.text
    assert response.json()["assignee_id"] == value
    assert state(client, response.json()["id"])[0] == response.json()


@pytest.mark.parametrize("value", list(ASSIGNABLE.values()), ids=list(ASSIGNABLE))
@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_patch_with_assignee_id_of_admin_member_or_null_is_accepted(client, task, actor, value):
    history = state(client, task["id"])[1]
    response = client.patch(f"/tasks/{task['id']}", json={"assignee_id": value}, headers=HEADERS[actor])
    assert response.status_code == 200, response.text
    assert state(client, task["id"]) == (response.json(), history)
    # Only the assignee and the update time change.
    assert {**response.json(), "updated_at": task["updated_at"]} == {**task, "assignee_id": value}
