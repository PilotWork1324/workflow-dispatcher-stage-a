"""PATCH stores a new title exactly as sent, as POST does (SPEC.md sections 2 and 3).

`title` is a nonblank Unicode string, 1–120 characters, preserved exactly. The spaces, tabs,
line breaks and other whitespace at the start and the end of a title are part of it: PATCH
stores and returns them unchanged, as POST does, and the task reads back with them. The limit
counts them too, so a title of 120 characters with its whitespace is kept whole and one of 121
is refused. A title of whitespace only is still blank, 422. A refused request changes nothing:
the task reads back as it was and no status-history event is written, even when the body also
carries valid fields and a status transition.
"""

import pytest
from fastapi.testclient import TestClient

from workflow.api import create_app


HEADERS = {name: {"Authorization": f"Bearer demo-{name}"} for name in ("admin", "alice")}
LIMIT = 120
# Whitespace at the start, at the end or at both; the last three are whitespace outside ASCII,
# which str.strip removes as well.
SURROUNDED = {
    "spaces": "  Plan the review  ",
    "trailing-tab": "Plan the review\t",
    "leading-line-break": "\nPlan the review",
    "tab-and-crlf": "\t Plan the review\r\n",
    "vertical-tab-and-form-feed": "\x0bPlan the review\x0c",
    "no-break-space": " Plan the review ",
    "em-and-ideographic-space": " Plan the review　",
    "line-separator": "Plan the review ",
}
REFUSED = {
    "blank-spaces-and-tab": " \t ",
    "blank-space": " ",
    "blank-line-break": "\n",
    "blank-mixed": "\r\n\t \x0b\x0c",
    # 121 characters, 119 without the spaces around them.
    "over-limit-with-spaces": " " + "a" * (LIMIT - 1) + " ",
    "over-limit": "a" * (LIMIT + 1),
}
EXTRAS = {"alone": {}, "with-valid-fields": {"description": "Changed", "priority": 5, "status": "in_progress"}}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "title.sqlite3")) as result:
        yield result


@pytest.fixture
def task(client):
    """Alice's todo task, assigned to her."""
    body = {"title": "Plan the review", "assignee_id": 2}
    response = client.post("/tasks", json=body, headers=HEADERS["alice"])
    assert response.status_code == 201, response.text
    return response.json()


def state(client, task_id):
    """The task and its status history as Alice reads them."""
    task = client.get(f"/tasks/{task_id}", headers=HEADERS["alice"])
    history = client.get(f"/tasks/{task_id}/history", headers=HEADERS["alice"])
    assert (task.status_code, history.status_code) == (200, 200), (task.text, history.text)
    return task.json(), history.json()


@pytest.mark.parametrize("title", list(SURROUNDED.values()), ids=list(SURROUNDED))
@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_patch_keeps_the_whitespace_around_a_title(client, task, actor, title):
    history = state(client, task["id"])[1]
    response = client.patch(f"/tasks/{task['id']}", json={"title": title}, headers=HEADERS[actor])
    assert response.status_code == 200, response.text
    assert response.json()["title"] == title
    assert state(client, task["id"]) == (response.json(), history)
    # Only the title and the update time change.
    assert {**response.json(), "updated_at": task["updated_at"]} == {**task, "title": title}


@pytest.mark.parametrize("title", list(SURROUNDED.values()), ids=list(SURROUNDED))
def test_patch_stores_a_title_as_post_does(client, task, title):
    created = client.post("/tasks", json={"title": title}, headers=HEADERS["alice"])
    assert created.status_code == 201, created.text
    patched = client.patch(f"/tasks/{task['id']}", json={"title": title}, headers=HEADERS["alice"])
    assert patched.status_code == 200, patched.text
    assert patched.json()["title"] == created.json()["title"] == title


@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_patch_with_title_of_exactly_the_limit_with_whitespace_is_kept_whole(client, task, actor):
    title = " " + "a" * (LIMIT - 2) + "\t"
    response = client.patch(f"/tasks/{task['id']}", json={"title": title}, headers=HEADERS[actor])
    assert response.status_code == 200, response.text
    assert response.json()["title"] == title
    assert len(state(client, task["id"])[0]["title"]) == LIMIT


@pytest.mark.parametrize("title", list(REFUSED.values()), ids=list(REFUSED))
@pytest.mark.parametrize("extra", list(EXTRAS.values()), ids=list(EXTRAS))
@pytest.mark.parametrize("actor", ["alice", "admin"])
def test_patch_with_blank_or_too_long_title_is_refused_and_changes_nothing(client, task, actor, extra, title):
    before = state(client, task["id"])
    assert before[0] == task
    response = client.patch(f"/tasks/{task['id']}", json={**extra, "title": title}, headers=HEADERS[actor])
    assert response.status_code == 422, response.text
    assert state(client, task["id"]) == before
    # The same body with a valid title is accepted: the title alone was refused.
    accepted = client.patch(f"/tasks/{task['id']}", json={**extra, "title": " Renamed "}, headers=HEADERS[actor])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["title"] == " Renamed "
