import csv
import io
import os
import sys
import json
import shutil
import calendar
import threading
from datetime import datetime, date, timedelta
from typing import Optional, List
import uuid
import uvicorn
import httpx
from fastapi import FastAPI, Depends, HTTPException, Query, UploadFile, File, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse, Response
from starlette.types import Scope
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_

from database import init_db, get_db, engine, DB_DIR
from models import PipelineStage, Source, Tag, Client, Note, Task, ActivityLog, ClientCustomField, Attachment, client_tags, AvitoToken, AvitoChat, AvitoMessage, AvitoItem, AvitoItemDailyStat, RejectionReason
from schemas import (
    StageCreate, StageResponse,
    SourceCreate, SourceResponse,
    TagCreate, TagResponse,
    ClientCreate, ClientUpdate, ClientResponse, NoteCreate, NoteUpdate, NoteResponse,
    TaskCreate, TaskUpdate, TaskResponse,
    ActivityLogResponse,
    CustomField, DashboardStats,
    BatchMove, BatchDelete, ImportResult, TaskListResponse,
    AvitoChatResponse, AvitoMessageResponse, AvitoSendMessage,
    AvitoItemResponse, StageStat, SyncItemsResult,
    RejectionReasonCreate, RejectionReasonUpdate, RejectionReasonResponse,
)

UPLOAD_DIR = os.path.join(DB_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Config (stored as JSON next to the DB)
CONFIG_PATH = os.path.join(DB_DIR, "crm_config.json")


def _load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _get_avito_credentials(db=None) -> tuple:
    eid = os.getenv("AVITO_CLIENT_ID", "")
    esec = os.getenv("AVITO_CLIENT_SECRET", "")
    if eid:
        return eid, esec
    cfg = _load_config()
    cid, csec = cfg.get("avito_client_id", ""), cfg.get("avito_client_secret", "")
    if cid:
        return cid, csec
    if db:
        token = db.query(AvitoToken).first()
        if token and token.avito_client_id:
            return token.avito_client_id, token.avito_client_secret
    return "", ""


AVITO_REDIRECT_URI = "http://127.0.0.1:8000/api/avito/callback"

app = FastAPI(title="CRM System")


init_db()

@app.on_event("startup")
def on_startup():
    _seed_stages()
    _seed_sources()
    _seed_rejection_reasons()
    threading.Thread(target=_refresh_avito_token, daemon=True).start()


def _seed_stages():
    from database import SessionLocal
    db = SessionLocal()
    try:
        if db.query(PipelineStage).count() == 0:
            for name, order, color in [
                ("Новая заявка", 0, "#3b82f6"),
                ("Контакт установлен", 1, "#8b5cf6"),
                ("Презентация", 2, "#f59e0b"),
                ("Переговоры", 3, "#ec4899"),
                ("Закрытие сделки", 4, "#10b981"),
                ("Отказ", 5, "#ef4444"),
            ]:
                db.add(PipelineStage(name=name, order=order, color=color))
            db.commit()
    finally:
        db.close()


def _seed_sources():
    from database import SessionLocal
    db = SessionLocal()
    try:
        if db.query(Source).count() == 0:
            for i, name in enumerate(["Сайт", "Звонок", "Почта", "Мессенджер", "Рекомендация", "Другое"]):
                db.add(Source(name=name, order=i))
            db.commit()
    finally:
        db.close()


def _seed_rejection_reasons():
    from database import SessionLocal
    db = SessionLocal()
    try:
        if db.query(RejectionReason).count() == 0:
            for i, name in enumerate(["Дорого", "Пропал/не отвечает", "Заказал у другого"]):
                db.add(RejectionReason(name=name, order=i))
            db.commit()
    finally:
        db.close()


@app.get("/api/rejection-reasons", response_model=List[RejectionReasonResponse])
def list_rejection_reasons(db: Session = Depends(get_db)):
    return db.query(RejectionReason).order_by(RejectionReason.order).all()


@app.post("/api/rejection-reasons", response_model=RejectionReasonResponse)
def create_rejection_reason(data: RejectionReasonCreate, db: Session = Depends(get_db)):
    max_order = db.query(func.max(RejectionReason.order)).scalar() or 0
    r = RejectionReason(name=data.name, order=max_order + 1)
    db.add(r); db.commit(); db.refresh(r)
    return r


@app.put("/api/rejection-reasons/{reason_id}", response_model=RejectionReasonResponse)
def update_rejection_reason(reason_id: int, data: RejectionReasonUpdate, db: Session = Depends(get_db)):
    r = db.query(RejectionReason).filter(RejectionReason.id == reason_id).first()
    if not r:
        raise HTTPException(404, "Reason not found")
    r.name = data.name
    db.commit(); db.refresh(r)
    return r


@app.delete("/api/rejection-reasons/{reason_id}")
def delete_rejection_reason(reason_id: int, db: Session = Depends(get_db)):
    r = db.query(RejectionReason).filter(RejectionReason.id == reason_id).first()
    if not r:
        raise HTTPException(404, "Reason not found")
    # Clear references from clients
    db.query(Client).filter(Client.rejection_reason_id == reason_id).update({"rejection_reason_id": None})
    db.delete(r); db.commit()
    return {"ok": True}


def _log(client_id, action, description, db):
    log = ActivityLog(client_id=client_id, action=action, description=description)
    db.add(log)


def _enrich_client(client):
    now = datetime.now().replace(microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    week_end = today_start + timedelta(days=7)
    tasks = client.tasks or []
    overdue = 0; today = 0; week = 0; later = 0
    for t in tasks:
        if t.completed or not t.due_date:
            if not t.completed and not t.due_date: later += 1
            continue
        d = t.due_date.replace(tzinfo=None)
        if d < today_start: overdue += 1
        elif d < tomorrow_start: today += 1
        elif d < week_end: week += 1
        else: later += 1
    client.task_count = len(tasks)
    client.overdue_count = overdue
    client.today_count = today
    client.week_count = week
    client.later_count = later
    logs = client.activity_log or []
    client.last_activity = logs[0].created_at if logs else None
    return client


# ---- Stages ----

@app.get("/api/stages", response_model=list[StageResponse])
def get_stages(db: Session = Depends(get_db)):
    stages = db.query(PipelineStage).order_by(PipelineStage.order).all()
    return [StageResponse(
        id=s.id, name=s.name, order=s.order, color=s.color,
        client_count=len(s.clients), total_budget=sum(c.budget for c in s.clients)
    ) for s in stages]


@app.post("/api/stages", response_model=StageResponse)
def create_stage(data: StageCreate, db: Session = Depends(get_db)):
    s = PipelineStage(name=data.name, order=data.order, color=data.color)
    db.add(s); db.commit(); db.refresh(s)
    return StageResponse(id=s.id, name=s.name, order=s.order, color=s.color, client_count=0, total_budget=0)


@app.put("/api/stages/{stage_id}", response_model=StageResponse)
def update_stage(stage_id: int, data: StageCreate, db: Session = Depends(get_db)):
    s = db.query(PipelineStage).filter(PipelineStage.id == stage_id).first()
    if not s:
        raise HTTPException(404, "Stage not found")
    s.name = data.name; s.order = data.order; s.color = data.color
    db.commit(); db.refresh(s)
    return StageResponse(id=s.id, name=s.name, order=s.order, color=s.color, client_count=len(s.clients), total_budget=sum(c.budget for c in s.clients))


@app.delete("/api/stages/{stage_id}")
def delete_stage(stage_id: int, db: Session = Depends(get_db)):
    s = db.query(PipelineStage).filter(PipelineStage.id == stage_id).first()
    if not s:
        raise HTTPException(404, "Stage not found")
    if s.clients:
        raise HTTPException(400, "Нельзя удалить этап с клиентами")
    db.delete(s); db.commit()
    return {"ok": True}


# ---- Sources ----

@app.get("/api/sources", response_model=list[SourceResponse])
def get_sources(db: Session = Depends(get_db)):
    return db.query(Source).order_by(Source.order).all()


@app.post("/api/sources", response_model=SourceResponse)
def create_source(data: SourceCreate, db: Session = Depends(get_db)):
    if db.query(Source).filter(Source.name == data.name).first():
        raise HTTPException(400, "Источник уже существует")
    s = Source(name=data.name, order=data.order)
    db.add(s); db.commit(); db.refresh(s)
    return s


@app.put("/api/sources/{source_id}", response_model=SourceResponse)
def update_source(source_id: int, data: SourceCreate, db: Session = Depends(get_db)):
    s = db.query(Source).filter(Source.id == source_id).first()
    if not s:
        raise HTTPException(404, "Source not found")
    s.name = data.name; s.order = data.order
    db.commit(); db.refresh(s)
    return s


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: int, db: Session = Depends(get_db)):
    s = db.query(Source).filter(Source.id == source_id).first()
    if not s:
        raise HTTPException(404, "Source not found")
    db.delete(s); db.commit()
    return {"ok": True}


# ---- Tags ----

@app.get("/api/tags", response_model=list[TagResponse])
def get_tags(db: Session = Depends(get_db)):
    return db.query(Tag).order_by(Tag.name).all()


@app.post("/api/tags", response_model=TagResponse)
def create_tag(data: TagCreate, db: Session = Depends(get_db)):
    if db.query(Tag).filter(Tag.name == data.name).first():
        raise HTTPException(400, "Тег уже существует")
    t = Tag(name=data.name, color=data.color)
    db.add(t); db.commit(); db.refresh(t)
    return t


@app.put("/api/tags/{tag_id}", response_model=TagResponse)
def update_tag(tag_id: int, data: TagCreate, db: Session = Depends(get_db)):
    t = db.query(Tag).filter(Tag.id == tag_id).first()
    if not t:
        raise HTTPException(404, "Tag not found")
    t.name = data.name; t.color = data.color
    db.commit(); db.refresh(t)
    return t


@app.delete("/api/tags/{tag_id}")
def delete_tag(tag_id: int, db: Session = Depends(get_db)):
    t = db.query(Tag).filter(Tag.id == tag_id).first()
    if not t:
        raise HTTPException(404, "Tag not found")
    db.delete(t); db.commit()
    return {"ok": True}


# ---- Clients ----

@app.get("/api/clients", response_model=list[ClientResponse])
def get_clients(
    stage_id: int | None = None,
    source: str | None = None,
    tag_id: int | None = None,
    query: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Client)
    if stage_id is not None:
        q = q.filter(Client.stage_id == stage_id)
    if source:
        q = q.filter(Client.source == source)
    if tag_id is not None:
        q = q.filter(Client.tags.any(Tag.id == tag_id))
    if query:
        like = f"%{query}%"
        q = q.filter(
            Client.name.ilike(like) | Client.phone.ilike(like) |
            Client.organization.ilike(like) | Client.email.ilike(like) |
            Client.responsible.ilike(like)
        )
    clients = q.order_by(Client.updated_at.desc()).all()
    for c in clients:
        _enrich_client(c)
    return clients


# ---- Merge ----

@app.get("/api/clients/duplicates")
def find_duplicates(field: str = "phone", db: Session = Depends(get_db)):
    if field == "phone":
        col = Client.phone
    elif field == "name":
        col = Client.name
    else:
        raise HTTPException(400, "Unsupported field")
    subq = db.query(col, func.count(Client.id).label("cnt")).group_by(col).having(func.count(Client.id) > 1).subquery()
    clients = db.query(Client).filter(col == subq.c[field]).order_by(col).all()
    groups = {}
    for c in clients:
        key = getattr(c, field)
        if key not in groups:
            groups[key] = []
        groups[key].append({"id": c.id, "name": c.name, "phone": c.phone, "organization": c.organization, "created_at": c.created_at})
    return [{"field": k, "clients": v} for k, v in groups.items()]


@app.post("/api/clients/merge")
def merge_clients(data: dict, db: Session = Depends(get_db)):
    keep_id = data.get("keep_id")
    merge_id = data.get("merge_id")
    if not keep_id or not merge_id:
        raise HTTPException(400, "keep_id and merge_id required")
    keep = db.query(Client).filter(Client.id == keep_id).first()
    merge = db.query(Client).filter(Client.id == merge_id).first()
    if not keep or not merge:
        raise HTTPException(404, "Client not found")
    for note in merge.notes:
        note.client_id = keep_id
    for task in merge.tasks:
        task.client_id = keep_id
    for log in merge.activity_log:
        log.client_id = keep_id
    for att in merge.attachments:
        att.client_id = keep_id
    for cf in merge.custom_fields:
        cf.client_id = keep_id
    for tag in merge.tags:
        if tag not in keep.tags:
            keep.tags.append(tag)
    if not keep.phone and merge.phone:
        keep.phone = merge.phone
    if not keep.email and merge.email:
        keep.email = merge.email
    if not keep.organization and merge.organization:
        keep.organization = merge.organization
    if not keep.address and merge.address:
        keep.address = merge.address
    db.delete(merge)
    _log(keep_id, "merged", f"Объединён с клиентом #{merge_id}", db)
    db.commit()
    return {"ok": True, "kept_id": keep_id}


# ---- Client CRUD ----

@app.get("/api/clients/{client_id}", response_model=ClientResponse)
def get_client(client_id: int, db: Session = Depends(get_db)):
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404, "Client not found")
    return _enrich_client(c)


@app.post("/api/clients", response_model=ClientResponse)
def create_client(data: ClientCreate, db: Session = Depends(get_db)):
    stage = db.query(PipelineStage).filter(PipelineStage.id == data.stage_id).first()
    if not stage:
        raise HTTPException(404, "Stage not found")
    client = Client(**data.model_dump(exclude={"custom_fields", "tag_ids"}))
    for cf in data.custom_fields:
        client.custom_fields.append(ClientCustomField(field_name=cf.field_name, field_value=cf.field_value))
    if data.tag_ids:
        tags = db.query(Tag).filter(Tag.id.in_(data.tag_ids)).all()
        client.tags = tags
    db.add(client); db.commit(); db.refresh(client)
    _log(client.id, "created", "Клиент создан", db)
    db.commit()
    return _enrich_client(client)


@app.put("/api/clients/{client_id}", response_model=ClientResponse)
def update_client(client_id: int, data: ClientUpdate, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Client not found")
    old_stage = client.stage_id
    for key, val in data.model_dump(exclude={"custom_fields", "tag_ids"}, exclude_none=True).items():
        setattr(client, key, val)
    if data.custom_fields is not None:
        client.custom_fields.clear()
        for cf in data.custom_fields:
            client.custom_fields.append(ClientCustomField(field_name=cf.field_name, field_value=cf.field_value))
    if data.tag_ids is not None:
        client.tags = db.query(Tag).filter(Tag.id.in_(data.tag_ids)).all() if data.tag_ids else []
    db.commit(); db.refresh(client)
    if old_stage != client.stage_id:
        new_stage = db.query(PipelineStage).filter(PipelineStage.id == client.stage_id).first()
        _log(client.id, "moved", f"Перемещён в этап «{new_stage.name}»", db)
        db.commit()
    return _enrich_client(client)


@app.delete("/api/clients/{client_id}")
def delete_client(client_id: int, db: Session = Depends(get_db)):
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404, "Client not found")
    db.delete(c); db.commit()
    return {"ok": True}


# ---- Batch Operations ----

@app.post("/api/clients/batch/move")
def batch_move(data: BatchMove, db: Session = Depends(get_db)):
    stage = db.query(PipelineStage).filter(PipelineStage.id == data.stage_id).first()
    if not stage:
        raise HTTPException(404, "Stage not found")
    clients = db.query(Client).filter(Client.id.in_(data.client_ids)).all()
    for c in clients:
        c.stage_id = data.stage_id
        _log(c.id, "moved", f"Массовое перемещение в «{stage.name}»", db)
    db.commit()
    return {"ok": True, "moved": len(clients)}


@app.post("/api/clients/batch/delete")
def batch_delete(data: BatchDelete, db: Session = Depends(get_db)):
    clients = db.query(Client).filter(Client.id.in_(data.client_ids)).all()
    for c in clients:
        db.delete(c)
    db.commit()
    return {"ok": True, "deleted": len(clients)}


# ---- Import CSV ----

@app.post("/api/import/csv")
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    imported = 0
    errors = []
    stages = {s.name: s for s in db.query(PipelineStage).all()}
    first_stage = db.query(PipelineStage).order_by(PipelineStage.order).first()
    for i, row in enumerate(reader, 2):
        try:
            name = (row.get("Имя") or row.get("name") or "").strip()
            phone = (row.get("Телефон") or row.get("phone") or "").strip()
            if not name or not phone:
                errors.append(f"Строка {i}: нет имени или телефона"); continue
            stage_name = (row.get("Этап") or row.get("stage") or "").strip()
            stage = stages.get(stage_name) or first_stage
            client = Client(
                name=name, phone=phone,
                email=(row.get("Email") or row.get("email") or "").strip(),
                organization=(row.get("Организация") or row.get("organization") or "").strip(),
                address=(row.get("Адрес") or row.get("address") or "").strip(),
                responsible=(row.get("Ответственный") or row.get("responsible") or "").strip(),
                budget=int(row.get("Бюджет") or row.get("budget") or 0),
                source=(row.get("Источник") or row.get("source") or "").strip(),
                stage_id=stage.id,
            )
            db.add(client); db.flush()
            imported += 1
        except Exception as e:
            errors.append(f"Строка {i}: {e}")
    db.commit()
    return ImportResult(imported=imported, errors=errors)


# ---- Notes ----

@app.get("/api/clients/{client_id}/notes", response_model=list[NoteResponse])
def get_notes(client_id: int, db: Session = Depends(get_db)):
    return db.query(Note).filter(Note.client_id == client_id).order_by(Note.created_at.desc()).all()


@app.post("/api/clients/{client_id}/notes", response_model=NoteResponse)
def create_note(client_id: int, data: NoteCreate, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Client not found")
    note = Note(client_id=client_id, content=data.content)
    db.add(note)
    db.commit(); db.refresh(note)
    return note


@app.put("/api/notes/{note_id}", response_model=NoteResponse)
def update_note(note_id: int, data: NoteUpdate, db: Session = Depends(get_db)):
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(404, "Note not found")
    note.content = data.content
    db.commit(); db.refresh(note)
    return note


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(404, "Note not found")
    db.delete(note); db.commit()
    return {"ok": True}


# ---- Tasks ----

@app.get("/api/tasks", response_model=TaskListResponse)
def get_all_tasks(db: Session = Depends(get_db)):
    now = datetime.now().replace(microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = today_start + timedelta(days=7)
    tomorrow_start = today_start + timedelta(days=1)
    all_tasks = (
        db.query(Task)
        .join(Client)
        .filter(Task.completed == False)
        .order_by(Task.due_date.asc().nullslast())
        .all()
    )
    def to_resp(t):
        return TaskResponse(
            id=t.id, client_id=t.client_id, client_name=t.client.name,
            title=t.title, due_date=t.due_date, completed=t.completed, created_at=t.created_at,
        )
    overdue = [to_resp(t) for t in all_tasks if t.due_date and t.due_date.replace(tzinfo=None) < today_start]
    today = [to_resp(t) for t in all_tasks if t.due_date and today_start <= t.due_date.replace(tzinfo=None) < tomorrow_start]
    week = [to_resp(t) for t in all_tasks if t.due_date and tomorrow_start <= t.due_date.replace(tzinfo=None) < week_end]
    later = [to_resp(t) for t in all_tasks if not t.due_date or t.due_date.replace(tzinfo=None) >= week_end]
    return TaskListResponse(overdue=overdue, today=today, week=week, later=later)


@app.get("/api/clients/{client_id}/tasks", response_model=list[TaskResponse])
def get_tasks(client_id: int, db: Session = Depends(get_db)):
    return db.query(Task).filter(Task.client_id == client_id).order_by(Task.created_at.desc()).all()


@app.post("/api/clients/{client_id}/tasks", response_model=TaskResponse)
def create_task(client_id: int, data: TaskCreate, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Client not found")
    task = Task(client_id=client_id, title=data.title, due_date=data.due_date)
    db.add(task)
    db.commit(); db.refresh(task)
    return task


@app.put("/api/tasks/{task_id}", response_model=TaskResponse)
def update_task(task_id: int, data: TaskUpdate, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(404, "Task not found")
    if data.title is not None:
        task.title = data.title
    if data.due_date is not None:
        task.due_date = data.due_date
    if data.completed is not None and data.completed != task.completed:
        task.completed = data.completed
    db.commit(); db.refresh(task)
    return task


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(404, "Task not found")
    db.delete(task); db.commit()
    return {"ok": True}


# ---- Dashboard ----

@app.get("/api/dashboard", response_model=DashboardStats)
def get_dashboard(db: Session = Depends(get_db)):
    now = datetime.now().replace(microsecond=0)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    total = db.query(Client).count()
    recent = db.query(Client).filter(Client.created_at >= seven_days_ago).count()

    stages = db.query(PipelineStage).order_by(PipelineStage.order).all()
    stage_dist = [{"name": s.name, "color": s.color, "count": len(s.clients)} for s in stages]

    source_data = db.query(Client.source, func.count(Client.id)).group_by(Client.source).all()
    source_dist = [{"name": s or "Без источника", "count": c} for s, c in source_data]

    total_tasks = db.query(Task).count()
    completed_tasks = db.query(Task).filter(Task.completed == True).count()
    overdue_tasks = db.query(Task).filter(Task.completed == False, Task.due_date != None, Task.due_date < now).count()
    clients_with_tasks = db.query(Task.client_id).distinct().count()

    # Budget
    budget_data = db.query(func.coalesce(func.sum(Client.budget), 0), func.coalesce(func.avg(Client.budget), 0)).filter(Client.budget > 0).first()
    total_budget = float(budget_data[0])
    avg_budget = round(float(budget_data[1]), 0) if budget_data[1] else 0
    budget_by_stage = []
    for s in stages:
        sb = db.query(func.coalesce(func.sum(Client.budget), 0)).filter(Client.stage_id == s.id).scalar()
        budget_by_stage.append({"name": s.name, "color": s.color, "budget": float(sb)})

    # By responsible
    resp_data = db.query(Client.responsible, func.count(Client.id)).filter(Client.responsible != None, Client.responsible != "").group_by(Client.responsible).order_by(func.count(Client.id).desc()).limit(10).all()
    by_responsible = [{"name": r or "Без ответственного", "count": c} for r, c in resp_data]

    # Activity
    active_7d = db.query(ActivityLog.client_id).distinct().filter(ActivityLog.created_at >= seven_days_ago).count()
    active_30d = db.query(ActivityLog.client_id).distinct().filter(ActivityLog.created_at >= thirty_days_ago).count()
    inactive_clients = total - db.query(ActivityLog.client_id).distinct().count()

    # Tags
    tag_data = (
        db.query(Tag.id, Tag.name, Tag.color, func.count(client_tags.c.client_id))
        .join(client_tags, Tag.id == client_tags.c.tag_id)
        .group_by(Tag.id)
        .order_by(func.count(client_tags.c.client_id).desc())
        .limit(10)
        .all()
    )
    tag_distribution = [{"name": t.name, "color": t.color, "count": c} for t, _, _, c in tag_data]

    # Monthly new clients (last 6 months)
    monthly = []
    for i in range(5, -1, -1):
        m_start = datetime(now.year, now.month, 1) - timedelta(days=30 * i)
        if m_start.month == 12:
            m_end = datetime(m_start.year + 1, 1, 1)
        else:
            m_end = datetime(m_start.year, m_start.month + 1, 1)
        cnt = db.query(Client).filter(Client.created_at >= m_start, Client.created_at < m_end).count()
        monthly.append({"month": m_start.month, "year": m_start.year, "count": cnt})

    # Task completion by stage
    task_stage = []
    for s in stages:
        total_s = db.query(Task).join(Client).filter(Client.stage_id == s.id).count()
        done_s = db.query(Task).join(Client).filter(Client.stage_id == s.id, Task.completed == True).count()
        task_stage.append({"name": s.name, "color": s.color, "total": total_s, "completed": done_s})

    return DashboardStats(
        total_clients=total, recent_clients=recent,
        stage_distribution=stage_dist, source_distribution=source_dist,
        total_tasks=total_tasks, completed_tasks=completed_tasks,
        overdue_tasks=overdue_tasks, no_task_clients=total - clients_with_tasks,
        total_budget=total_budget, avg_budget=avg_budget,
        budget_by_stage=budget_by_stage,
        by_responsible=by_responsible,
        active_7d=active_7d, active_30d=active_30d,
        inactive_clients=inactive_clients,
        tag_distribution=tag_distribution,
        monthly_clients=monthly,
        task_completion_by_stage=task_stage,
    )


# ---- Export ----

@app.get("/api/export/csv")
def export_csv(db: Session = Depends(get_db)):
    clients = db.query(Client).order_by(Client.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Имя", "Телефон", "Email", "Организация", "Адрес", "Ответственный", "Бюджет", "Источник", "Этап", "Создан", "Обновлён"])
    for c in clients:
        sn = c.stage.name if c.stage else ""
        writer.writerow([c.id, c.name, c.phone, c.email, c.organization, c.address, c.responsible, c.budget, c.source, sn, c.created_at, c.updated_at])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=clients.csv"})


# ---- Calendar ----

@app.get("/api/calendar/tasks")
def calendar_tasks(year: int, month: int, db: Session = Depends(get_db)):
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    tasks = (
        db.query(Task)
        .join(Client)
        .filter(Task.due_date >= start, Task.due_date < end)
        .order_by(Task.due_date)
        .all()
    )
    days = {}
    for t in tasks:
        d = t.due_date.replace(tzinfo=None).day
        if d not in days:
            days[d] = []
        days[d].append({
            "id": t.id, "client_id": t.client_id, "client_name": t.client.name,
            "title": t.title, "completed": t.completed,
        })
    return {"year": year, "month": month, "days": days}


# ---- Attachments ----

@app.get("/api/clients/{client_id}/attachments")
def get_attachments(client_id: int, db: Session = Depends(get_db)):
    return db.query(Attachment).filter(Attachment.client_id == client_id).order_by(Attachment.created_at.desc()).all()


@app.post("/api/clients/{client_id}/attachments")
async def upload_attachment(client_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Client not found")
    ext = os.path.splitext(file.filename)[1] if "." in file.filename else ""
    stored = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, stored)
    content = await file.read()
    with open(path, "wb") as f:
        f.write(content)
    att = Attachment(client_id=client_id, filename=stored, original_name=file.filename, file_size=len(content))
    db.add(att)
    db.commit(); db.refresh(att)
    return att


@app.get("/api/attachments/{att_id}/download")
def download_attachment(att_id: int, db: Session = Depends(get_db)):
    att = db.query(Attachment).filter(Attachment.id == att_id).first()
    if not att:
        raise HTTPException(404, "Attachment not found")
    path = os.path.join(UPLOAD_DIR, att.filename)
    if not os.path.exists(path):
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=att.original_name)


@app.delete("/api/attachments/{att_id}")
def delete_attachment(att_id: int, db: Session = Depends(get_db)):
    att = db.query(Attachment).filter(Attachment.id == att_id).first()
    if not att:
        raise HTTPException(404, "Attachment not found")
    cid = att.client_id
    path = os.path.join(UPLOAD_DIR, att.filename)
    if os.path.exists(path):
        os.remove(path)
    db.delete(att)
    db.commit()
    return {"ok": True}


# ---- Backup / Restore ----

@app.get("/api/backup")
def backup_db():
    db_path = "crm.db"
    if not os.path.exists(db_path):
        raise HTTPException(404, "Database not found")
    return FileResponse(db_path, filename=f"crm_backup_{date.today().isoformat()}.db", media_type="application/octet-stream")


@app.post("/api/restore")
async def restore_db(file: UploadFile = File(...)):
    content = await file.read()
    db_path = "crm.db"
    backup_path = f"crm.db.bak"
    if os.path.exists(db_path):
        shutil.copy2(db_path, backup_path)
    with open(db_path, "wb") as f:
        f.write(content)
    return {"ok": True, "message": "БД восстановлена. Перезапустите приложение."}


# ---- Avito Integration ----

from avito_api.messenger import SyncMessengerClient
from avito_api.config import ClientConfig
import avito_api.exceptions as avito_err


def _make_avito_client(db: Session):
    """Build a SyncMessengerClient from stored credentials and token."""
    token = db.query(AvitoToken).first()
    if not token:
        return None
    cid, csec = _get_avito_credentials()
    if not cid or not csec:
        return None
    cfg = ClientConfig()
    cfg.api.auto_refresh_token = True
    client = SyncMessengerClient(
        client_id=cid,
        client_secret=csec,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        token_expires_at=int(token.expires_at.timestamp()),
        config=cfg,
    )
    return client


def _save_avito_token(db: Session, client: SyncMessengerClient):
    """Persist token updates made by the SDK auto-refresh."""
    try:
        info = client.get_current_token()
        token = db.query(AvitoToken).first()
        if token and info.access_token and info.access_token != token.access_token:
            token.access_token = info.access_token
            if info.expires_at:
                token.expires_at = datetime.fromtimestamp(info.expires_at)
            db.commit()
    except Exception:
        pass


def _sync_avito_chats(db: Session, client: SyncMessengerClient, user_id: str, token: AvitoToken = None):
    try:
        resp = client.get_chats(user_id=int(user_id), limit=100)
        _save_avito_token(db, client)
    except Exception:
        return
    for c in resp.chats:
        chat_id = str(c.id)
        if not chat_id:
            continue
        ctx = c.context
        item = ctx.value if ctx else None

        # Skip chats about other people's ads
        if item and item.user_id and item.user_id != int(user_id):
            # Remove from DB if already present
            existing = db.query(AvitoChat).filter(AvitoChat.chat_id == chat_id).first()
            if existing:
                db.query(AvitoMessage).filter(AvitoMessage.chat_id == chat_id).delete()
                db.delete(existing)
            continue

        users = c.users or []
        other_user = users[0] if users else None
        other_id = str(other_user.id) if other_user and other_user.id else ""
        other_name = other_user.name or "" if other_user else ""

        item_id_val = item.id if item else None

        existing = db.query(AvitoChat).filter(AvitoChat.chat_id == chat_id).first()
        if not existing:
            existing = AvitoChat(chat_id=chat_id, avito_user_id=user_id)
            db.add(existing)
            existing.item_title = item.title if item else ""
            existing.item_url = str(item.url) if item and item.url else ""
            existing.item_image = next(iter(item.images), "") if item and item.images else ""
            existing.avito_item_id = item_id_val
            existing.other_user_name = other_name
            existing.other_user_phone = ""
            existing.unread_count = 0

            client_name = other_name or f"Клиент Авито ({chat_id[:8]})"
            first_stage = db.query(PipelineStage).order_by(PipelineStage.order).first()
            stage_id = first_stage.id if first_stage else 1
            existing_client = Client(
                name=client_name,
                phone="",
                source="Авито",
                stage_id=stage_id,
            )
            db.add(existing_client)
            title = existing.item_title or ""
            if title:
                existing_client.notes.append(Note(content=f"Объявление: {title}"))
            db.flush()
            existing.client_id = existing_client.id
        else:
            existing.unread_count = 0
            if item_id_val is not None:
                existing.avito_item_id = item_id_val
            if item:
                existing.item_title = item.title or existing.item_title
                existing.item_url = str(item.url) if item.url else existing.item_url

        if c.last_message:
            try:
                existing.last_message_at = datetime.fromtimestamp(c.last_message.created)
            except Exception:
                pass

        # Sync messages only for new chats or active (client-linked) chats
        is_active = existing.client_id is not None
        if not existing.client_id and existing.id:  # first sync — just created, sync anyway
            is_active = True
        if is_active:
            try:
                msgs = client.get_messages(user_id=int(user_id), chat_id=chat_id, limit=100)
                _save_avito_token(db, client)
                for m in msgs.messages:
                    mid = str(m.id)
                    if not mid:
                        continue
                    existing_msg = db.query(AvitoMessage).filter(AvitoMessage.message_id == mid).first()
                    if existing_msg:
                        # Update read status on re-sync
                        if m.is_read is True and not existing_msg.is_read:
                            existing_msg.is_read = True
                            try:
                                existing_msg.read_at = datetime.fromtimestamp(m.read) if m.read else datetime.now()
                            except Exception:
                                existing_msg.read_at = datetime.now()
                        continue
                    is_ours = str(m.author_id) == str(user_id) if m.author_id else False
                    msg = AvitoMessage(
                        chat_id=chat_id,
                        message_id=mid,
                        author_id=str(m.author_id) if m.author_id else "",
                        author_name="Вы" if is_ours else "",
                        content=(m.content.text or "") if m.content else "",
                        payload="",
                        is_read=bool(m.is_read) if m.is_read else False,
                    )
                    try:
                        msg.created_at = datetime.fromtimestamp(m.created)
                    except Exception:
                        msg.created_at = datetime.now()
                    if m.is_read and m.read:
                        try:
                            msg.read_at = datetime.fromtimestamp(m.read)
                        except Exception:
                            pass
                    db.add(msg)
            except Exception:
                pass
    db.commit()


_stats_sync_state = {"running": False, "total": 0, "synced": 0, "error": None}
_sync_lock = threading.Lock()


def _fetch_single_day(db, token, day):
    """Fetch stats for one day from Avito API, save to DB. Returns True on success."""
    import time
    from datetime import datetime
    day_start = datetime.combine(day, datetime.min.time())
    day_str = day.isoformat()
    headers = {"Authorization": f"Bearer {token.access_token}", "Content-Type": "application/json"}

    for attempt in range(3):
        try:
            r = httpx.post(
                f"https://api.avito.ru/stats/v2/accounts/{token.user_id}/items",
                headers=headers,
                json={"dateFrom": day_str, "dateTo": day_str, "metrics": ["views", "contacts", "favorites", "impressions", "presenceSpending"], "grouping": "item", "limit": 500, "offset": 0},
                timeout=30,
            )
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"Stats rate-limited for {day_str}, retry in {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code == 200:
                body = r.json()
                wrapper = body.get("result") or body
                groupings = wrapper.get("groupings") if isinstance(wrapper, dict) else []
                for g in groupings if isinstance(groupings, list) else []:
                    if not isinstance(g, dict):
                        continue
                    sid = g.get("id")
                    if sid is None:
                        continue
                    metrics = g.get("metrics") or []
                    mapped = {}
                    for m in metrics:
                        if isinstance(m, dict):
                            mapped[m.get("slug")] = m.get("value")
                    if not isinstance(mapped.get("impressions"), (int, float)):
                        continue
                    spent_val = mapped.get("presenceSpending")
                    if isinstance(spent_val, (int, float)):
                        spent_val = spent_val / 100.0
                    existing = db.query(AvitoItemDailyStat).filter(
                        AvitoItemDailyStat.avito_item_id == int(sid),
                        AvitoItemDailyStat.date == day_start,
                    ).first()
                    if existing:
                        existing.impressions = mapped.get("impressions")
                        existing.views = mapped.get("views")
                        existing.contacts = mapped.get("contacts")
                        existing.favorites = mapped.get("favorites")
                        existing.spent = spent_val
                    else:
                        db.add(AvitoItemDailyStat(
                            avito_item_id=int(sid),
                            date_from=day_start,
                            date=day_start,
                            impressions=mapped.get("impressions"),
                            views=mapped.get("views"),
                            contacts=mapped.get("contacts"),
                            favorites=mapped.get("favorites"),
                            spent=spent_val,
                        ))
                db.commit()
                return True
            print(f"Stats API error for {day_str}: {r.status_code} {r.text[:100]}")
            return False
        except Exception as e:
            print(f"Stats fetch failed for {day_str}: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    return False


def _background_sync_stats(token_id, date_from, date_to, total_days):
    """Background thread: sync one day per API call, respecting rate limits."""
    from database import SessionLocal
    import time
    db = SessionLocal()
    try:
        token = db.query(AvitoToken).filter(AvitoToken.id == token_id).first()
        items = db.query(AvitoItem).all()
        if not token or not items:
            return

        current = date_from
        while current <= date_to and _stats_sync_state.get("running"):
            with _sync_lock:
                _stats_sync_state["synced"] += 1

            success = _fetch_single_day(db, token, current)
            if success:
                now = datetime.now()
                for it in items:
                    it.stats_updated_at = now
                db.commit()

            current += timedelta(days=1)
            if current <= date_to:
                time.sleep(65)  # 1 req/min rate limit, 5s buffer

        with _sync_lock:
            _stats_sync_state["running"] = False
        print(f"Background sync complete: {date_from.isoformat()}..{date_to.isoformat()}")
    except Exception as e:
        with _sync_lock:
            _stats_sync_state["running"] = False
            _stats_sync_state["error"] = str(e)
        print(f"Background sync failed: {e}")
    finally:
        db.close()


def _get_sync_progress():
    with _sync_lock:
        return dict(_stats_sync_state)


_syncing_items = False


def _refresh_avito_token():
    """Re-acquire Avito token on startup to ensure all scopes are present."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        token = db.query(AvitoToken).first()
        if not token:
            return
        cid, csec = _get_avito_credentials(db)
        if not cid or not csec:
            return
        r = httpx.post("https://api.avito.ru/token", data={
            "client_id": cid,
            "client_secret": csec,
            "grant_type": "client_credentials",
            "scope": "items:info messenger:read messenger:write stats:read",
        })
        data = r.json()
        if "error" not in data:
            token.access_token = data["access_token"]
            token.refresh_token = ""
            token.expires_at = datetime.now() + timedelta(seconds=data.get("expires_in", 86400))
            db.commit()
    except Exception:
        pass
    finally:
        db.close()


@app.post("/api/avito/sync-items", response_model=SyncItemsResult)
def avito_sync_items(db: Session = Depends(get_db)):
    global _syncing_items
    if _syncing_items:
        raise HTTPException(429, "Синхронизация уже выполняется")
    token = db.query(AvitoToken).first()
    if not token:
        raise HTTPException(400, "Avito не подключён")
    _syncing_items = True
    try:
        headers = {"Authorization": f"Bearer {token.access_token}"}
        synced = 0
        total = 0
        page = 1
        while True:
            r = httpx.get(
                "https://api.avito.ru/core/v1/items",
                headers=headers,
                params={"page": page, "per_page": 100},
                timeout=30,
            )
            if r.status_code != 200:
                break
            body = r.json()
            resources = body.get("resources", [])
            if not resources:
                break
            total += len(resources)
            for res in resources:
                item_id = res.get("id")
                if not item_id:
                    continue
                title = res.get("title", "")
                address = res.get("address", "")
                url = str(res.get("url", ""))
                price = res.get("price")
                status = res.get("status", "")
                cat = res.get("category", {}) or {}
                category = cat.get("name", "") if isinstance(cat, dict) else ""
                placed_at = None
                for dt_key in ("start_time", "published_at", "created_at", "startTime", "publishedAt", "createdAt", "date"):
                    raw = res.get(dt_key)
                    if raw:
                        try:
                            placed_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                            break
                        except (ValueError, TypeError):
                            continue
                existing = db.query(AvitoItem).filter(AvitoItem.avito_item_id == item_id).first()
                if existing:
                    existing.title = title
                    existing.address = address
                    existing.url = url
                    existing.price = price
                    existing.status = status
                    existing.category = category
                    if placed_at:
                        existing.placed_at = placed_at
                else:
                    db.add(AvitoItem(
                        avito_item_id=item_id,
                        title=title, address=address, url=url,
                        price=price, status=status, category=category,
                        placed_at=placed_at,
                    ))
                synced += 1
            if len(resources) < 100:
                break
            page += 1
        # Backfill avito_item_id on existing chats
        chats_to_fix = db.query(AvitoChat).filter(AvitoChat.avito_item_id.is_(None)).all()
        import re
        for chat in chats_to_fix:
            if chat.item_url:
                m = re.search(r'avito\.ru/(?:\w+/)?(\d+)(?:\?|$|#)', chat.item_url)
                if m:
                    found_id = int(m.group(1))
                    item = db.query(AvitoItem).filter(AvitoItem.avito_item_id == found_id).first()
                    if item:
                        chat.avito_item_id = found_id
        db.commit()
        return SyncItemsResult(synced=synced, total=total)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Ошибка синхронизации: {e}")
    finally:
        _syncing_items = False


@app.get("/api/avito/items", response_model=List[AvitoItemResponse])
def avito_get_items(date_from: str = Query(...), date_to: str = Query(...), db: Session = Depends(get_db)):
    items = db.query(AvitoItem).order_by(AvitoItem.updated_at.desc()).all()
    if not items:
        return []
    return _build_avito_item_responses(items, date_from, date_to, db)


@app.get("/api/avito/stats-info")
def avito_stats_info(db: Session = Depends(get_db)):
    last = db.query(AvitoItemDailyStat.date).order_by(AvitoItemDailyStat.date.desc()).first()
    count = db.query(AvitoItemDailyStat).count()
    progress = _get_sync_progress()
    return {
        "has_data": count > 0,
        "daily_rows": count,
        "last_date": last[0].isoformat()[:10] if last else None,
        "sync_running": progress.get("running", False),
        "sync_total": progress.get("total", 0),
        "sync_synced": progress.get("synced", 0),
        "sync_error": progress.get("error"),
    }


@app.post("/api/avito/sync-stats")
def avito_sync_stats(days: int = Query(default=30), db: Session = Depends(get_db)):
    token = db.query(AvitoToken).first()
    if not token:
        raise HTTPException(400, "Avito не подключён")
    with _sync_lock:
        if _stats_sync_state["running"]:
            raise HTTPException(429, "Синхронизация уже выполняется")
    from datetime import date as date_type, timedelta
    today = date_type.today()
    date_from = today - timedelta(days=days - 1)
    total_days = (today - date_from).days + 1
    db.query(AvitoItemDailyStat).delete()
    db.commit()
    with _sync_lock:
        _stats_sync_state["running"] = True
        _stats_sync_state["total"] = total_days
        _stats_sync_state["synced"] = 0
        _stats_sync_state["error"] = None
    threading.Thread(target=_background_sync_stats, args=(token.id, date_from, today, total_days), daemon=True).start()
    return {"ok": True, "status": "started", "total_days": total_days}


@app.post("/api/avito/refresh-stats")
def avito_refresh_stats(mode: str = Query(default=None), db: Session = Depends(get_db)):
    token = db.query(AvitoToken).first()
    if not token:
        raise HTTPException(400, "Avito не подключён")
    with _sync_lock:
        if _stats_sync_state["running"]:
            raise HTTPException(429, "Синхронизация уже выполняется")
    from datetime import date as date_type, timedelta
    today = date_type.today()

    last_row = db.query(AvitoItemDailyStat.date).order_by(AvitoItemDailyStat.date.desc()).first()

    if mode is None:
        if last_row:
            last_date = last_row[0].date()
            gap = (today - last_date).days
            if gap > 30:
                return {"need_choice": True, "last_date": last_date.isoformat()}
            if gap <= 1:
                return {"ok": True, "synced_days": 0}
            date_from = last_date + timedelta(days=1)
        else:
            return {"need_choice": True, "last_date": None}
    elif mode == "month_begin":
        date_from = date_type(today.year, today.month, 1)
    elif mode == "30d":
        date_from = today - timedelta(days=29)
    elif mode == "90d":
        date_from = today - timedelta(days=89)
    else:
        raise HTTPException(400, "Unknown mode")

    total_days = (today - date_from).days + 1
    with _sync_lock:
        _stats_sync_state["running"] = True
        _stats_sync_state["total"] = total_days
        _stats_sync_state["synced"] = 0
        _stats_sync_state["error"] = None
    threading.Thread(target=_background_sync_stats, args=(token.id, date_from, today, total_days), daemon=True).start()
    return {"ok": True, "status": "started", "total_days": total_days}


def _build_avito_item_responses(items, date_from, date_to, db):
    from datetime import date as date_type, datetime
    df = date_type.fromisoformat(date_from[:10])
    dt = date_type.fromisoformat(date_to[:10])
    df_dt = datetime.combine(df, datetime.min.time())
    dt_dt = datetime.combine(dt, datetime.min.time())

    item_ids = [i.avito_item_id for i in items if i.avito_item_id]
    rows = db.query(
        AvitoItemDailyStat.avito_item_id,
        func.sum(AvitoItemDailyStat.impressions).label("impressions"),
        func.sum(AvitoItemDailyStat.views).label("views"),
        func.sum(AvitoItemDailyStat.contacts).label("contacts"),
        func.sum(AvitoItemDailyStat.favorites).label("favorites"),
        func.sum(AvitoItemDailyStat.spent).label("spent"),
    ).filter(
        AvitoItemDailyStat.avito_item_id.in_(item_ids),
        AvitoItemDailyStat.date >= df_dt,
        AvitoItemDailyStat.date <= dt_dt,
    ).group_by(AvitoItemDailyStat.avito_item_id).all() if item_ids else []

    agg = {r.avito_item_id: r for r in rows}
    result = []
    for it in items:
        r = agg.get(it.avito_item_id)
        impressions = r.impressions if r else None
        views = r.views if r else None
        contacts = r.contacts if r else None
        favorites = r.favorites if r else None
        spent = r.spent if r else None
        price_per_view = round(spent / views, 2) if (spent and views and views > 0) else None
        price_per_contact = round(spent / contacts, 2) if (spent and contacts and contacts > 0) else None
        stage_q = db.query(
            PipelineStage.name, PipelineStage.color, func.count(Client.id).label("cnt")
        ).select_from(AvitoChat).join(Client, AvitoChat.client_id == Client.id
        ).join(PipelineStage, Client.stage_id == PipelineStage.id
        ).filter(AvitoChat.avito_item_id == it.avito_item_id
        ).group_by(PipelineStage.id, PipelineStage.name, PipelineStage.color).all()
        result.append(AvitoItemResponse(
            avito_item_id=it.avito_item_id,
            title=it.title or "",
            address=it.address or "",
            url=it.url or "",
            price=it.price,
            status=it.status or "",
            category=it.category or "",
            placed_at=it.placed_at,
            impressions=impressions,
            views=views,
            contacts=contacts,
            favorites=favorites,
            spent=spent,
            price_per_view=price_per_view,
            price_per_contact=price_per_contact,
            stats_updated_at=it.stats_updated_at,
            stage_stats=[StageStat(stage_name=s.name, color=s.color, count=s.cnt) for s in stage_q],
        ))
    return result


@app.post("/api/avito/connect")
def avito_connect(db: Session = Depends(get_db)):
    cid, csec = _get_avito_credentials()
    if not cid or not csec:
        raise HTTPException(400, "Сначала укажите Client ID и Client Secret в разделе Интеграции")
    try:
        r = httpx.post("https://api.avito.ru/token", data={
            "client_id": cid,
            "client_secret": csec,
            "grant_type": "client_credentials",
            "scope": "items:info messenger:read messenger:write stats:read",
        })
        data = r.json()
        if "error" in data:
            raise HTTPException(400, data.get("error_description", data["error"]))
        token_val = data["access_token"]
        expires_in = data.get("expires_in", 86400)
        # Get user info
        r2 = httpx.get("https://api.avito.ru/core/v1/accounts/self",
                       headers={"Authorization": f"Bearer {token_val}"})
        if r2.status_code != 200:
            raise HTTPException(502, "Не удалось получить информацию о пользователе")
        user = r2.json()
        uid = str(user.get("id", ""))
        comp_id = str(user.get("company_id", "")) if "company_id" in user else ""
        db.query(AvitoToken).delete()
        new_token = AvitoToken(
            access_token=token_val,
            refresh_token="",
            expires_at=datetime.now() + timedelta(seconds=expires_in),
            user_id=uid,
            company_id=comp_id,
            avito_client_id=cid,
            avito_client_secret=csec,
        )
        db.add(new_token)
        db.commit()
        return {"ok": True, "user_id": uid}
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Ошибка подключения: {e}")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/avito/status")
def avito_status(db: Session = Depends(get_db)):
    token = db.query(AvitoToken).first()
    cfg = _load_config()
    return {
        "connected": token is not None,
        "user_id": token.user_id if token else "",
        "avito_client_id": cfg.get("avito_client_id", "") or (token.avito_client_id if token else ""),
        "avito_client_secret": cfg.get("avito_client_secret", "") or (token.avito_client_secret if token else ""),
    }


@app.post("/api/avito/sync")
def avito_sync(db: Session = Depends(get_db)):
    client = _make_avito_client(db)
    if not client:
        raise HTTPException(400, "Avito не подключён")
    token = db.query(AvitoToken).first()
    if not token:
        raise HTTPException(400, "Avito не подключён")
    try:
        _sync_avito_chats(db, client, token.user_id, token)
        if token.company_id and token.company_id != token.user_id:
            _sync_avito_chats(db, client, token.company_id, token)
    except Exception as e:
        raise HTTPException(500, f"Ошибка синхронизации: {e}")
    return {"ok": True}


@app.post("/api/avito/disconnect")
def avito_disconnect(db: Session = Depends(get_db)):
    db.query(AvitoToken).delete()
    db.commit()
    return {"ok": True}


@app.get("/api/avito/chats", response_model=List[AvitoChatResponse])
def avito_chats(db: Session = Depends(get_db)):
    chats = db.query(AvitoChat).order_by(AvitoChat.last_message_at.desc().nullslast()).all()
    result = []
    for c in chats:
        client_name = c.client.name if c.client else ""
        last_msg = db.query(AvitoMessage).filter(AvitoMessage.chat_id == c.chat_id).order_by(AvitoMessage.created_at.desc()).first()
        preview = last_msg.content[:100] if last_msg else ""
        result.append(AvitoChatResponse(
            id=c.id, chat_id=c.chat_id, client_id=c.client_id, client_name=client_name,
            other_user_name=c.other_user_name, other_user_phone=c.other_user_phone,
            item_title=c.item_title, item_url=c.item_url, item_image=c.item_image,
            last_message_preview=preview, last_message_at=c.last_message_at,
            unread_count=c.unread_count, created_at=c.created_at,
        ))
    return result


@app.get("/api/avito/chats/{chat_id}/messages", response_model=List[AvitoMessageResponse])
def avito_chat_messages(chat_id: str, db: Session = Depends(get_db)):
    return db.query(AvitoMessage).filter(AvitoMessage.chat_id == chat_id).order_by(AvitoMessage.created_at).all()


@app.post("/api/avito/chats/{chat_id}/messages")
def avito_send_message(chat_id: str, data: AvitoSendMessage, db: Session = Depends(get_db)):
    client = _make_avito_client(db)
    if not client:
        raise HTTPException(400, "Avito не подключён")
    chat = db.query(AvitoChat).filter(AvitoChat.chat_id == chat_id).first()
    if not chat:
        raise HTTPException(404, "Chat not found")
    token = db.query(AvitoToken).first()
    if not token:
        raise HTTPException(400, "Avito не подключён")
    try:
        sent = client.send_message(user_id=int(token.user_id), chat_id=chat_id, text=data.content)
        _save_avito_token(db, client)
        msg = AvitoMessage(
            chat_id=chat_id, message_id=str(sent.id) or uuid.uuid4().hex,
            author_id=token.user_id, author_name="Вы",
            content=data.content, payload="",
            is_read=False,
            created_at=datetime.now(),
        )
        db.add(msg)
        db.commit()
        return msg
    except avito_err.AvitoApiError as e:
        raise HTTPException(502, f"Ошибка отправки: {e}")


@app.get("/api/avito/chats/{chat_id}/client-link")
def avito_link_client(chat_id: str, client_id: int = Query(...), db: Session = Depends(get_db)):
    chat = db.query(AvitoChat).filter(AvitoChat.chat_id == chat_id).first()
    if not chat:
        raise HTTPException(404, "Chat not found")
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Client not found")
    chat.client_id = client_id
    db.commit()
    return {"ok": True}


@app.get("/api/avito/client/{client_id}/messages", response_model=List[AvitoMessageResponse])
def avito_client_messages(client_id: int, db: Session = Depends(get_db)):
    chats = db.query(AvitoChat).filter(AvitoChat.client_id == client_id).all()
    if not chats:
        return []
    chat_ids = [c.chat_id for c in chats]
    return db.query(AvitoMessage).filter(AvitoMessage.chat_id.in_(chat_ids)).order_by(AvitoMessage.created_at).all()


@app.post("/api/avito/client/{client_id}/sync-messages")
def avito_sync_client_messages(client_id: int, db: Session = Depends(get_db)):
    """Sync Avito messages for a client's chat, returning imported count."""
    chat = db.query(AvitoChat).filter(AvitoChat.client_id == client_id).first()
    if not chat:
        raise HTTPException(404, "Чат Авито не найден")
    c = _make_avito_client(db)
    if not c:
        raise HTTPException(400, "Avito не подключён")
    token = db.query(AvitoToken).first()
    if not token:
        raise HTTPException(400, "Avito не подключён")
    imported = 0
    try:
        msgs = c.get_messages(user_id=int(token.user_id), chat_id=chat.chat_id, limit=100)
        _save_avito_token(db, c)
        for m in msgs.messages:
            mid = str(m.id)
            if not mid:
                continue
            existing_msg = db.query(AvitoMessage).filter(AvitoMessage.message_id == mid).first()
            if existing_msg:
                if m.is_read is True and not existing_msg.is_read:
                    existing_msg.is_read = True
                    try:
                        existing_msg.read_at = datetime.fromtimestamp(m.read) if m.read else datetime.now()
                    except Exception:
                        existing_msg.read_at = datetime.now()
                    db.commit()
                continue
            is_ours = str(m.author_id) == str(token.user_id) if m.author_id else False
            msg = AvitoMessage(
                chat_id=chat.chat_id, message_id=mid,
                author_id=str(m.author_id) if m.author_id else "",
                author_name="Вы" if is_ours else "",
                content=(m.content.text or "") if m.content else "",
                payload="",
                is_read=bool(m.is_read) if m.is_read else False,
            )
            try:
                msg.created_at = datetime.fromtimestamp(m.created)
            except Exception:
                msg.created_at = datetime.now()
            if m.is_read and m.read:
                try:
                    msg.read_at = datetime.fromtimestamp(m.read)
                except Exception:
                    pass
            db.add(msg)
            imported += 1
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Avito message sync error: {e}")
    return {"imported": imported}


@app.get("/api/avito/client/{client_id}/chat")
def avito_client_chat(client_id: int, db: Session = Depends(get_db)):
    chat = db.query(AvitoChat).filter(AvitoChat.client_id == client_id).first()
    if not chat:
        return None
    return {"chat_id": chat.chat_id, "item_title": chat.item_title, "item_url": chat.item_url}


# ---- Config API ----
@app.put("/api/config")
def update_config(data: dict = Body(...), db: Session = Depends(get_db)):
    allowed = {"avito_client_id", "avito_client_secret"}
    cfg = _load_config()
    for k, v in data.items():
        if k in allowed:
            cfg[k] = str(v)
    _save_config(cfg)
    # Also persist to DB
    token = db.query(AvitoToken).first()
    if token:
        for k, v in data.items():
            if k == "avito_client_id":
                token.avito_client_id = str(v)
            elif k == "avito_client_secret":
                token.avito_client_secret = str(v)
        db.commit()
    return {"ok": True}


# ---- Static ----

def _static_dir():
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "static")
    return os.path.join(os.path.dirname(__file__), "static")

class _NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

app.mount("/", _NoCacheStaticFiles(directory=_static_dir(), html=True), name="static")


def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    run_server()
