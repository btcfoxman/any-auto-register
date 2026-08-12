from sqlmodel import Session, select

from api.task_commands import RegisterTaskRequest
from application.tasks import TASK_STATUS_SUCCEEDED, create_register_task
from core.db import TaskModel, engine


def test_register_request_accepts_account_expansion_parameters():
    body = RegisterTaskRequest(
        platform="freebeat",
        count=100,
        concurrency=1,
        use_proxy_pool=True,
        deduplicate_active=True,
    )

    payload = body.model_dump()
    assert payload["platform"] == "freebeat"
    assert payload["count"] == 100
    assert payload["concurrency"] == 1
    assert payload["use_proxy_pool"] is True
    assert payload["deduplicate_active"] is True


def test_register_task_reuses_active_task_when_deduplication_is_enabled():
    payload = {
        "platform": "freebeat",
        "count": 100,
        "concurrency": 1,
        "use_proxy_pool": True,
        "deduplicate_active": True,
    }

    first = create_register_task(payload)
    second = create_register_task(payload)

    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert second["task_id"] == first["task_id"]
    with Session(engine) as session:
        tasks = session.exec(select(TaskModel)).all()
    assert len(tasks) == 1


def test_register_task_creates_new_task_after_previous_task_finishes():
    payload = {
        "platform": "freebeat",
        "count": 100,
        "deduplicate_active": True,
    }
    first = create_register_task(payload)
    with Session(engine) as session:
        task = session.get(TaskModel, first["task_id"])
        assert task is not None
        task.status = TASK_STATUS_SUCCEEDED
        session.add(task)
        session.commit()

    second = create_register_task(payload)

    assert second["deduplicated"] is False
    assert second["task_id"] != first["task_id"]
