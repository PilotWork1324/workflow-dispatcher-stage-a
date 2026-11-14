"""PATCH enforces the description limit as POST does (SPEC.md sections 2 and 5).

`description` is a string up to 2,000 characters. A longer one is invalid input, 422, on
update as on creation, and the refused request changes nothing: the task reads back as it
was and no status-history event is written, even when the body also carries valid fields
and a status transition. Exactly 2,000 characters is accepted. The limit counts characters,
not UTF-8 bytes, so a 2,000-character description of non-ASCII letters is accepted too.
"""

import pytest
from fastapi.testclient import TestClient

from workflow.api import create_app


HEADERS = {name: {"Authorization": f"Bearer demo-{name}"} for name in ("admin", "alice")}
LIMIT = 2000
# A one-byte and a two-byte letter in UTF-8.
LETTERS = {"ascii": "a", "cyrillic": "ж"}
EXTRAS = {"alone": {}, "with-valid-fields": {"title": "Renamed", "priority": 5, "status": "in_progress"}}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "description.sqlite3")) as result:
        yield result


@pytest.fixture
def task(client):
    """Alice's todo task, assigned to her, with a short description."""
    body = {"title": "Description limit", "description": "Before", "assignee_id": 2}
    response = client.post("/tasks", json=body, headers=HEADERS["alice"])
    assert response.status_code == 201, response.text
    return response.json()


def state(client, task_id):
    """The task and its status history as Alice reads them."""
    task = client.get(f"/tasks/{task_id}", headers=HEADERS["alice"])
    history = client.get(f"/tasks/{task_id}/history", headers=HEADERS["alice"])
    assert (task.status_code, history.status_code) == (200, 200), (task.text, history.text)
    return task.json(), history.json()


@pytest.mark.parametrize("letter", list(LETTERS.values()), ids=list(LETTERS))
@pytest.mark.parametrize("extra", list(EXTRAS.values()), ids=list(EXTRAS))
@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_patch_with_description_over_limit_is_refused_and_changes_nothing(client, task, actor, extra, letter):
    before = state(client, task["id"])
    assert before[0] == task
    response = client.patch(f"/tasks/{task['id']}", json={**extra, "description": letter * (LIMIT + 1)},
                            headers=HEADERS[actor])
    assert response.status_code == 422, response.text
    assert state(client, task["id"]) == before
    # The same body one character shorter is accepted: the length alone was refused.
    accepted = client.patch(f"/tasks/{task['id']}", json={**extra, "description": letter * LIMIT},
                            headers=HEADERS[actor])
    assert accepted.status_code == 200, accepted.text


@pytest.mark.parametrize("letter", list(LETTERS.values()), ids=list(LETTERS))
@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_patch_with_description_of_exactly_the_limit_is_accepted(client, task, actor, letter):
    description = letter * LIMIT
    history = state(client, task["id"])[1]
    response = client.patch(f"/tasks/{task['id']}", json={"description": description}, headers=HEADERS[actor])
    assert response.status_code == 200, response.text
    assert state(client, task["id"]) == (response.json(), history)
    # Only the description and the update time change.
    assert {**response.json(), "updated_at": task["updated_at"]} == {**task, "description": description}


@pytest.mark.parametrize("letter", list(LETTERS.values()), ids=list(LETTERS))
def test_create_with_description_over_limit_is_refused(client, task, letter):
    response = client.post("/tasks", json={"title": "Too long", "description": letter * (LIMIT + 1)},
                           headers=HEADERS["alice"])
    assert response.status_code == 422, response.text
    listed = client.get("/tasks", headers=HEADERS["admin"]).json()
    assert ([row["id"] for row in listed["items"]], listed["total"]) == ([task["id"]], 1)
