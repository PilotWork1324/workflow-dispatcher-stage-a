"""PATCH stores a title exactly as sent, as POST does (SPEC.md sections 2 and 3).

`title` is a nonblank Unicode string of 1 to 120 characters, preserved exactly. PATCH keeps
the spaces, tabs, line breaks and other whitespace at the start and the end of a new title:
the response, GET /tasks/{id}, GET /tasks and a restarted application all return it as sent,
the same title POST stores for the same text. The padding counts toward the 120 characters.
A title of whitespace only, or longer than 120 characters, is still invalid input, 422, and
the refused request changes nothing: the task reads back as it was and no status-history
event is written, even when the body also carries valid fields and a status transition.
"""

import pytest
from fastapi.testclient import TestClient

from workflow.api import create_app


HEADERS = {name: {"Authorization": f"Bearer demo-{name}"} for name in ("admin", "alice")}
# Each title has characters Python's str.strip() removes at one end or both.
PADDED = {
    "spaces-both-ends": "  Plan the review  ",
    "trailing-tab": "Plan the review\t",
    "leading-tab": "\tPlan the review",
    "line-breaks": "\nPlan the review\r\n",
    "mixed": " \t\r\n Plan the review \n\t ",
    "vertical-tab-form-feed": "\x0bPlan the review\x0c",
    "unicode-spaces": " Plan the review　",
    "separators": "\x1cPlan the review ",
}
BLANK = {"space": " ", "space-tab-space": " \t ", "line-breaks": "\r\n", "mixed": "\t\n "}
TOO_LONG = {"121-plain": "x" * 121, "121-padded": " " + "x" * 119 + " ", "121-trailing-tab": "x" * 120 + "\t"}
EXTRAS = {"alone": {}, "with-valid-fields": {"description": "Changed", "priority": 5}}
REFUSED_EXTRAS = {"alone": {}, "with-valid-fields": {"description": "Changed", "priority": 5, "status": "in_progress"}}


@pytest.fixture
def path(tmp_path):
    return tmp_path / "title.sqlite3"


@pytest.fixture
def client(path):
    with TestClient(create_app(path)) as result:
        yield result


@pytest.fixture
def task(client):
    """Alice's todo task, created as the issue does."""
    response = client.post("/tasks", json={"title": "Plan the review"}, headers=HEADERS["alice"])
    assert response.status_code == 201, response.text
    return response.json()


def state(client, task_id):
    """The task and its status history as the administrator reads them."""
    task = client.get(f"/tasks/{task_id}", headers=HEADERS["admin"])
    history = client.get(f"/tasks/{task_id}/history", headers=HEADERS["admin"])
    assert (task.status_code, history.status_code) == (200, 200), (task.text, history.text)
    return task.json(), history.json()


def listed_titles(client):
    """Each task's ID and title as GET /tasks lists them for the administrator."""
    response = client.get("/tasks", headers=HEADERS["admin"])
    assert response.status_code == 200, response.text
    return {row["id"]: row["title"] for row in response.json()["items"]}


@pytest.mark.parametrize("title", list(PADDED.values()), ids=list(PADDED))
@pytest.mark.parametrize("extra", list(EXTRAS.values()), ids=list(EXTRAS))
@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_patch_keeps_the_whitespace_around_a_title(client, task, actor, extra, title):
    history = state(client, task["id"])[1]
    response = client.patch(f"/tasks/{task['id']}", json={**extra, "title": title}, headers=HEADERS[actor])
    assert response.status_code == 200, response.text
    assert response.json()["title"] == title
    assert state(client, task["id"]) == (response.json(), history)
    # Only the title, the other fields sent and the update time change.
    assert {**response.json(), "updated_at": task["updated_at"]} == {**task, **extra, "title": title}


@pytest.mark.parametrize("title", list(PADDED.values()), ids=list(PADDED))
def test_patch_stores_the_title_post_stores_and_it_survives_a_restart(path, client, task, title):
    created = client.post("/tasks", json={"title": title}, headers=HEADERS["alice"])
    assert created.status_code == 201, created.text
    patched = client.patch(f"/tasks/{task['id']}", json={"title": title}, headers=HEADERS["alice"])
    assert patched.status_code == 200, patched.text
    assert patched.json()["title"] == created.json()["title"] == title
    expected = {task["id"]: title, created.json()["id"]: title}
    assert listed_titles(client) == expected
    with TestClient(create_app(path)) as restarted:
        assert listed_titles(restarted) == expected
        assert state(restarted, task["id"])[0] == patched.json()


@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_patch_title_of_120_characters_with_padding_is_kept_whole(client, task, actor):
    title = "  " + "x" * 116 + "  "
    assert len(title) == 120
    response = client.patch(f"/tasks/{task['id']}", json={"title": title}, headers=HEADERS[actor])
    assert response.status_code == 200, response.text
    assert state(client, task["id"])[0]["title"] == title


@pytest.mark.parametrize("title", list(BLANK.values()) + list(TOO_LONG.values()), ids=list(BLANK) + list(TOO_LONG))
@pytest.mark.parametrize("extra", list(REFUSED_EXTRAS.values()), ids=list(REFUSED_EXTRAS))
@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_patch_with_blank_or_too_long_title_is_refused_and_changes_nothing(client, task, actor, extra, title):
    before = state(client, task["id"])
    assert before[0] == task
    response = client.patch(f"/tasks/{task['id']}", json={**extra, "title": title}, headers=HEADERS[actor])
    assert response.status_code == 422, response.text
    assert state(client, task["id"]) == before
    # The same body with a valid title is accepted: the title alone was refused.
    accepted = client.patch(f"/tasks/{task['id']}", json={**extra, "title": "Renamed"}, headers=HEADERS[actor])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["title"] == "Renamed"
