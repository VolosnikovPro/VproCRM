import csv
import io
import os
import sys
import json
import re
import shutil
import sqlite3
import time
import calendar
import threading
from copy import deepcopy
from datetime import datetime, date, timedelta
from typing import Optional, List
import uuid
import uvicorn
import httpx
from fastapi import FastAPI, Depends, HTTPException, Query, UploadFile, File, Body
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse, Response
from starlette.types import Scope
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_, exists

from database import init_db, get_db, engine, DB_DIR
from models import PipelineStage, Source, Tag, Client, Note, Task, ActivityLog, ClientCustomField, Attachment, client_tags, AvitoToken, AvitoChat, AvitoMessage, AvitoItem, AvitoItemDailyStat, RejectionReason, AvitoGroup, Notification
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
    AvitoItemResponse, StageStat, SyncItemsResult, NotificationItem, NotificationsResponse,
    RejectionReasonCreate, RejectionReasonUpdate, RejectionReasonResponse,
    AvitoGroupCreate, AvitoGroupUpdate, AvitoGroupResponse, AvitoGroupStats, MoveItemsRequest,
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

app = FastAPI(title="VProCRM")


init_db()

@app.on_event("startup")
def on_startup():
    from database import SessionLocal
    db = SessionLocal()
    try:
        _seed_stages(db)
        _seed_sources(db)
        _seed_rejection_reasons(db)
        _seed_default_groups(db)
        _rename_group_if_exists("Мобильная разработка", "IT разработка", db)
        _auto_assign_groups(db)
    finally:
        db.close()
    threading.Thread(target=_refresh_avito_token, daemon=True).start()


def _seed_stages(db=None):
    if db is None:
        from database import SessionLocal
        db = SessionLocal()
        own_session = True
    else:
        own_session = False
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
        if own_session:
            db.close()


def _seed_sources(db=None):
    if db is None:
        from database import SessionLocal
        db = SessionLocal()
        own_session = True
    else:
        own_session = False
    try:
        if db.query(Source).count() == 0:
            for i, name in enumerate(["Сайт", "Звонок", "Почта", "Мессенджер", "Рекомендация", "Другое"]):
                db.add(Source(name=name, order=i))
            db.commit()
    finally:
        if own_session:
            db.close()


def _seed_rejection_reasons(db=None):
    if db is None:
        from database import SessionLocal
        db = SessionLocal()
        own_session = True
    else:
        own_session = False
    try:
        if db.query(RejectionReason).count() == 0:
            for i, name in enumerate(["Дорого", "Пропал/не отвечает", "Заказал у другого"]):
                db.add(RejectionReason(name=name, order=i))
            db.commit()
    finally:
        if own_session:
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
    day_after_start = today_start + timedelta(days=2)
    week_end = today_start + timedelta(days=7)
    tasks = client.tasks or []
    overdue = 0; today = 0; tomorrow = 0; week = 0; later = 0
    for t in tasks:
        if t.completed or not t.due_date:
            if not t.completed and not t.due_date: later += 1
            continue
        d = t.due_date.replace(tzinfo=None)
        if d < today_start: overdue += 1
        elif d < tomorrow_start: today += 1
        elif d < day_after_start: tomorrow += 1
        elif d < week_end: week += 1
        else: later += 1
    client.task_count = len(tasks)
    client.overdue_count = overdue
    client.today_count = today
    client.tomorrow_count = tomorrow
    client.week_count = week
    client.later_count = later
    logs = client.activity_log or []
    client.last_activity = logs[0].created_at if logs else None
    return client


# ---- Stages ----

@app.get("/api/stages", response_model=list[StageResponse])
def get_stages(db: Session = Depends(get_db)):
    stages = db.query(PipelineStage).order_by(PipelineStage.order).all()
    reject_stage = db.query(PipelineStage).filter(PipelineStage.name == "Отказ").first()
    reject_stage_id = reject_stage.id if reject_stage else -1
    return [StageResponse(
        id=s.id, name=s.name, order=s.order, color=s.color,
        client_count=len(s.clients),
        total_budget=sum(c.budget for c in s.clients if s.id != reject_stage_id)
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
    reject_stage = db.query(PipelineStage).filter(PipelineStage.name == "Отказ").first()
    reject_stage_id = reject_stage.id if reject_stage else -1
    return StageResponse(id=s.id, name=s.name, order=s.order, color=s.color, client_count=len(s.clients), total_budget=sum(c.budget for c in s.clients if s.id != reject_stage_id))


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
    no_tasks: bool = False,
    tomorrow: bool = False,
    db: Session = Depends(get_db),
):
    q = db.query(Client)
    if stage_id is not None:
        q = q.filter(Client.stage_id == stage_id)
    if source:
        q = q.filter(Client.source == source)
    if tag_id is not None:
        q = q.filter(Client.tags.any(Tag.id == tag_id))
    if no_tasks:
        q = q.filter(~Client.tasks.any())
    if query:
        like = f"%{query}%"
        avito_subq = exists().select_from(AvitoMessage).join(
            AvitoChat, AvitoMessage.chat_id == AvitoChat.chat_id
        ).where(
            AvitoChat.client_id == Client.id,
            AvitoMessage.content.ilike(like)
        )
        q = q.filter(
            Client.name.ilike(like) | Client.phone.ilike(like) |
            Client.organization.ilike(like) | Client.email.ilike(like) |
            Client.responsible.ilike(like) |
            Client.notes.any(Note.content.ilike(like)) |
            avito_subq
        )
    clients = q.order_by(Client.updated_at.desc()).all()
    for c in clients:
        _enrich_client(c)
    # Archive: hide Отказ deals older than 7 days
    reject_stage = db.query(PipelineStage).filter(PipelineStage.name == "Отказ").first()
    reject_stage_id = reject_stage.id if reject_stage else -1
    cutoff = datetime.now() - timedelta(days=7)
    clients = [c for c in clients if not (c.stage_id == reject_stage_id and c.updated_at < cutoff)]
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
    for chat in db.query(AvitoChat).filter(AvitoChat.client_id == merge_id).all():
        chat.client_id = keep_id
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
    name = client.deal_name or client.name or "Клиент"
    _create_notification(db, "new_client", f"Новый клиент: {name}", client.id)
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
        # Track stages passed
        passed = json.loads(client.stages_passed or "[]")
        all_stages = db.query(PipelineStage).order_by(PipelineStage.order).all()
        stage_ids = [s.id for s in all_stages]
        new_idx = stage_ids.index(client.stage_id) if client.stage_id in stage_ids else -1
        old_idx = stage_ids.index(old_stage) if old_stage in stage_ids else -1
        # If moving backwards, remove subsequent stages from passed
        if old_idx > new_idx >= 0:
            passed = [sid for sid in passed if sid not in stage_ids[new_idx + 1:]]
        new_stage = db.query(PipelineStage).filter(PipelineStage.id == client.stage_id).first()
        is_rejection = new_stage and new_stage.name == "Отказ"
        if not is_rejection and new_idx >= 0:
            # Auto-fill all stages up to current (except rejection)
            for sid in stage_ids[:new_idx + 1]:
                if sid not in passed:
                    passed.append(sid)
        elif client.stage_id not in passed:
            passed.append(client.stage_id)
        client.stages_passed = json.dumps(passed)
        db.commit()
        new_stage = db.query(PipelineStage).filter(PipelineStage.id == client.stage_id).first()
        if new_stage and new_stage.name == "Отказ" and client.rejection_reason_id:
            reason = db.query(RejectionReason).filter(RejectionReason.id == client.rejection_reason_id).first()
            reason_suffix = f" (причина: {reason.name})" if reason else ""
            _log(client.id, "moved", f"Перемещён в этап «{new_stage.name}»{reason_suffix}", db)
        else:
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
    if data.result_text is not None:
        task.result_text = data.result_text
    if data.completed is not None and data.completed != task.completed:
        task.completed = data.completed
        if data.completed:
            task.completed_at = datetime.now().replace(microsecond=0)
            desc = f"Задача выполнена: {task.title}"
            if data.result_text:
                desc += f" — {data.result_text}"
            _log(task.client_id, "task", desc, db)
        else:
            task.completed_at = None
            _log(task.client_id, "task", f"Задача возобновлена: {task.title}", db)
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

    # Budget (exclude Отказ)
    reject_stage = db.query(PipelineStage).filter(PipelineStage.name == "Отказ").first()
    reject_stage_id = reject_stage.id if reject_stage else -1
    budget_data = db.query(func.coalesce(func.sum(Client.budget), 0), func.coalesce(func.avg(Client.budget), 0)).filter(Client.budget > 0, Client.stage_id != reject_stage_id).first()
    total_budget = float(budget_data[0])
    avg_budget = round(float(budget_data[1]), 0) if budget_data[1] else 0
    budget_by_stage = []
    for s in stages:
        if s.id == reject_stage_id:
            budget_by_stage.append({"name": s.name, "color": s.color, "budget": 0})
            continue
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
        month = now.month - i
        year = now.year
        while month <= 0:
            month += 12
            year -= 1
        m_start = datetime(year, month, 1)
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
    tmp_path = f"crm.db.restore_tmp"
    backup_path = f"crm.db.bak"
    with open(tmp_path, "wb") as f:
        f.write(content)
    try:
        conn = sqlite3.connect(tmp_path)
        conn.execute("SELECT COUNT(*) FROM sqlite_master")
        conn.close()
    except Exception as e:
        os.remove(tmp_path)
        raise HTTPException(400, f"Файл повреждён: {e}")
    if os.path.exists(db_path):
        shutil.copy2(db_path, backup_path)
    engine.dispose()
    os.replace(tmp_path, db_path)
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
    except Exception as e:
        print(f"Avito sync warning: {e}")


def _sync_avito_chats(db: Session, client: SyncMessengerClient, user_id: str, token: AvitoToken = None):
    try:
        resp = client.get_chats(user_id=int(user_id), limit=100)
        _save_avito_token(db, client)
    except Exception as e:
        print(f"Avito sync warning: {e}")
        return
    for c in resp.chats:
        chat_id = str(c.id)
        if not chat_id:
            continue
        ctx = c.context
        item = ctx.value if ctx else None

        # Skip chats without a valid ad item (system messages, deleted ads)
        if not item or not item.id:
            existing = db.query(AvitoChat).filter(AvitoChat.chat_id == chat_id).first()
            if existing:
                db.query(AvitoMessage).filter(AvitoMessage.chat_id == chat_id).delete()
                db.delete(existing)
            continue

        # Skip chats about other people's ads
        if item.user_id and item.user_id != int(user_id):
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
        is_new_client = False
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
            title = existing.item_title or ""
            existing_client = Client(
                name=client_name,
                phone="",
                source="Авито",
                stage_id=stage_id,
                deal_name=title,
            )
            db.add(existing_client)
            if title:
                existing_client.notes.append(Note(content=f"Объявление: {title}"))
            db.flush()
            existing.client_id = existing_client.id
            is_new_client = True
        else:
            # preserve existing unread_count — will be recomputed after message sync
            if item_id_val:
                existing.avito_item_id = item_id_val
            if item:
                existing.item_title = item.title or existing.item_title
                existing.item_url = str(item.url) if item.url else existing.item_url

        if c.last_message:
            try:
                existing.last_message_at = datetime.fromtimestamp(c.last_message.created)
            except Exception as e:
                print(f"Avito sync warning: {e}")

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
                        api_read = bool(m.is_read) if m.is_read else False
                        # Also check read timestamp as fallback
                        if not api_read and m.read:
                            api_read = True
                        if api_read != existing_msg.is_read:
                            existing_msg.is_read = api_read
                            if api_read and m.read:
                                try:
                                    existing_msg.read_at = datetime.fromtimestamp(m.read)
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
                        is_read=bool(m.is_read) if (m.is_read or m.read) else False,
                    )
                    try:
                        msg.created_at = datetime.fromtimestamp(m.created)
                    except Exception:
                        msg.created_at = datetime.now()
                    if m.read:
                        try:
                            msg.read_at = datetime.fromtimestamp(m.read)
                        except Exception as e:
                            print(f"Avito sync warning: {e}")
                    db.add(msg)
                    # Create notification for new client message (only if unread, skip rejected)
                    if not is_ours and existing.client_id and existing.client and not msg.is_read:
                        if existing.client.stage and existing.client.stage.name == "Отказ":
                            pass
                        else:
                            parts = [s for s in [existing.client.deal_name or "", existing.client.name or "Клиент"] if s]
                            notif_msg = f"Авито: {', '.join(parts)}"
                            txt = (m.content.text or "") if m.content else ""
                            if txt:
                                txt = txt[:100]
                                if len((m.content.text or "") if m.content else "") > 100:
                                    txt += "..."
                                notif_msg += f" — {txt}"
                            _create_notification(db, "avito_message", notif_msg, existing.client_id)
                # Recompute unread count from local messages
                if existing.client_id:
                    unread = db.query(AvitoMessage).filter(
                        AvitoMessage.chat_id == chat_id,
                        AvitoMessage.is_read == False,
                        AvitoMessage.author_id != str(user_id),
                    ).count()
                    existing.unread_count = unread
                # For new clients, set created_at to first message timestamp
                if is_new_client and existing.client_id:
                    first_msg = db.query(AvitoMessage).filter(
                        AvitoMessage.chat_id == chat_id
                    ).order_by(AvitoMessage.created_at.asc()).first()
                    if first_msg and first_msg.created_at:
                        client_obj = db.query(Client).filter(Client.id == existing.client_id).first()
                        if client_obj:
                            client_obj.created_at = first_msg.created_at
            except Exception as e:
                print(f"Avito sync warning: {e}")
    db.commit()


_stats_sync_state = {"running": False, "total": 0, "synced": 0, "error": None}
_sync_lock = threading.Lock()


def _fetch_single_day(db, token, day):
    """Fetch stats for one day from Avito API, save to DB. Returns True on success."""
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
        return deepcopy(_stats_sync_state)


def _get_avito_user_id(token):
    return token.user_id or token.company_id


_syncing_items = False
_syncing_items_lock = threading.Lock()
_last_active_check = 0


def _refresh_item_activity():
    """Fetch active item IDs from Avito API and update is_active. Runs at most once per 5 minutes."""
    global _last_active_check
    from time import time
    now = time()
    if now - _last_active_check < 300:
        return
    _last_active_check = now
    from database import SessionLocal
    db = SessionLocal()
    try:
        token = db.query(AvitoToken).first()
        if not token:
            return
        headers = {"Authorization": f"Bearer {token.access_token}"}
        active_ids = set()
        page = 1
        while True:
            r = httpx.get("https://api.avito.ru/core/v1/items", headers=headers,
                          params={"page": page, "per_page": 100}, timeout=30)
            if r.status_code != 200:
                break
            resources = r.json().get("resources", [])
            if not resources:
                break
            for res in resources:
                iid = res.get("id")
                if iid:
                    active_ids.add(iid)
            if len(resources) < 100:
                break
            page += 1
        db.query(AvitoItem).update({"is_active": False})
        if active_ids:
            db.query(AvitoItem).filter(AvitoItem.avito_item_id.in_(active_ids)).update(
                {"is_active": True}, synchronize_session=False
            )
        db.commit()
    except Exception as e:
        print(f"Avito sync warning: {e}")
    finally:
        db.close()


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
    except Exception as e:
        print(f"Avito sync warning: {e}")
    finally:
        db.close()


def _seed_default_groups(db=None):
    if db is None:
        from database import SessionLocal
        db = SessionLocal()
        own_session = True
    else:
        own_session = False
    try:
        if db.query(AvitoGroup).count() > 0:
            return
        groups = ["TG боты", "Макс боты", "Мобильные приложения", "PWA", "IT разработка", "Неопределено"]
        for i, name in enumerate(groups):
            db.add(AvitoGroup(name=name, sort_order=i))
        db.commit()
    finally:
        if own_session:
            db.close()


def _rename_group_if_exists(old_name, new_name, db=None):
    if db is None:
        from database import SessionLocal
        db = SessionLocal()
        own_session = True
    else:
        own_session = False
    try:
        group = db.query(AvitoGroup).filter(AvitoGroup.name == old_name).first()
        if group:
            group.name = new_name
            db.commit()
    finally:
        if own_session:
            db.close()

def _auto_assign_groups(db=None):
    if db is None:
        from database import SessionLocal
        db = SessionLocal()
        own_session = True
    else:
        own_session = False
    try:
        items = db.query(AvitoItem).all()
        if not items:
            return
        groups = {g.name.lower(): g.id for g in db.query(AvitoGroup).all()}
        other = db.query(AvitoGroup).filter(AvitoGroup.name == "Неопределено").first()
        rules = [
            (["pwa", "progressive web"], "pwa"),
            (["tg", "telegram", "t.me"], "tg боты"),
            (["макс", "max"], "макс боты"),
            (["мобильная разработк", "it разработк", "it"], "it разработка"),
            (["мобильн", "ios", "android", "flutter", "react native", "прилож"], "мобильные приложения"),
        ]
        for item in items:
            title_lower = (item.title or "").lower()
            assigned = False
            for keywords, group_name in rules:
                if any(kw in title_lower for kw in keywords):
                    item.group_id = groups.get(group_name)
                    assigned = True
                    break
            if not assigned and other:
                item.group_id = other.id
        db.commit()
    finally:
        if own_session:
            db.close()


@app.post("/api/avito/sync-items", response_model=SyncItemsResult)
def avito_sync_items(db: Session = Depends(get_db)):
    global _syncing_items
    with _syncing_items_lock:
        if _syncing_items:
            raise HTTPException(429, "Синхронизация уже выполняется")
        _syncing_items = True
    token = db.query(AvitoToken).first()
    if not token:
        raise HTTPException(400, "Avito не подключён")
    try:
        headers = {"Authorization": f"Bearer {token.access_token}"}
        # Mark all items inactive — active ones will be flipped back during sync
        db.query(AvitoItem).update({"is_active": False})
        db.commit()
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
                    existing.is_active = True
                    if placed_at:
                        existing.placed_at = placed_at
                else:
                    db.add(AvitoItem(
                        avito_item_id=item_id,
                        title=title, address=address, url=url,
                        price=price, status=status, category=category,
                        placed_at=placed_at, is_active=True,
                    ))
                synced += 1
            if len(resources) < 100:
                break
            page += 1
        # Backfill avito_item_id on existing chats
        chats_to_fix = db.query(AvitoChat).filter(AvitoChat.avito_item_id.is_(None)).all()
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
    _refresh_item_activity()
    items = db.query(AvitoItem).order_by(AvitoItem.updated_at.desc()).all()
    if not items:
        return []
    return _build_avito_item_responses(items, date_from, date_to, db)


@app.post("/api/avito/items/{item_id}/restore")
def avito_restore_item(item_id: int, db: Session = Depends(get_db)):
    token = db.query(AvitoToken).first()
    if not token or not token.user_id:
        raise HTTPException(400, "Avito не подключён")
    _refresh_avito_token()
    db.refresh(token)
    headers = {"Authorization": f"Bearer {token.access_token}"}
    try:
        r = httpx.get(
            f"https://api.avito.ru/core/v1/accounts/{token.user_id}/items/{item_id}/",
            headers=headers, timeout=30
        )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Ошибка Avito API: {e}")
    if r.status_code == 404:
        return {"ok": False, "status": "not_found", "url": None, "message": "Объявление не найдено на Avito"}
    if r.status_code != 200:
        raise HTTPException(502, f"Avito API вернул {r.status_code}: {r.text[:200]}")
    data = r.json()
    status = data.get("status")
    url = data.get("url")
    status_labels = {
        "active": "активно", "removed": "снято", "old": "истекло",
        "blocked": "заблокировано", "rejected": "отклонено модератором",
        "not_found": "не найдено", "another_user": "другой пользователь"
    }
    label = status_labels.get(status, status)
    if status == "active":
        item = db.query(AvitoItem).filter(AvitoItem.avito_item_id == item_id).first()
        if item:
            item.is_active = True
            db.commit()
        return {"ok": True, "active": True, "url": url}
    return {"ok": False, "active": False, "status": status, "url": url, "message": f"Статус на Avito: {label}"}


class BulkRestoreRequest(BaseModel):
    item_ids: List[int]


@app.post("/api/avito/items/bulk-restore")
def avito_bulk_restore(body: BulkRestoreRequest, db: Session = Depends(get_db)):
    token = db.query(AvitoToken).first()
    if not token or not token.user_id:
        raise HTTPException(400, "Avito не подключён")
    _refresh_avito_token()
    db.refresh(token)
    headers = {"Authorization": f"Bearer {token.access_token}"}
    status_labels = {
        "active": "активно", "removed": "снято", "old": "истекло",
        "blocked": "заблокировано", "rejected": "отклонено",
        "not_found": "не найдено", "another_user": "другой пользователь"
    }
    restored = []
    failed = []
    for item_id in body.item_ids:
        try:
            r = httpx.get(
                f"https://api.avito.ru/core/v1/accounts/{token.user_id}/items/{item_id}/",
                headers=headers, timeout=30
            )
            if r.status_code == 200:
                data = r.json()
                status = data.get("status")
                url = data.get("url")
                if status == "active":
                    item = db.query(AvitoItem).filter(AvitoItem.avito_item_id == item_id).first()
                    if item:
                        item.is_active = True
                        restored.append({"id": item_id, "title": item.title})
                    continue
                item = db.query(AvitoItem).filter(AvitoItem.avito_item_id == item_id).first()
                failed.append({
                    "id": item_id,
                    "title": item.title if item else str(item_id),
                    "url": url,
                    "message": status_labels.get(status, status)
                })
            else:
                failed.append({"id": item_id, "title": str(item_id), "url": None, "message": f"HTTP {r.status_code}"})
        except Exception as e:
            failed.append({"id": item_id, "title": str(item_id), "url": None, "message": str(e)[:50]})
    db.commit()
    return {
        "restored": restored,
        "failed": failed,
        "hasUrls": any(f.get("url") for f in failed),
    }


@app.get("/api/avito/stats-info")
def avito_stats_info(db: Session = Depends(get_db)):
    last = db.query(AvitoItemDailyStat.date).order_by(AvitoItemDailyStat.date.desc()).first()
    count = db.query(AvitoItemDailyStat).count()
    progress = _get_sync_progress()
    # Get last sync attempt time from items
    item = db.query(AvitoItem.stats_updated_at).order_by(AvitoItem.stats_updated_at.desc()).first()
    return {
        "has_data": count > 0,
        "daily_rows": count,
        "last_date": last[0].isoformat()[:10] if last else None,
        "last_data_date": last[0].isoformat()[:10] if last else None,
        "last_sync_attempt": item[0].isoformat()[:19] if item and item[0] else None,
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
    today = date.today()
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
    today = date.today()

    last_row = db.query(AvitoItemDailyStat.date).order_by(AvitoItemDailyStat.date.desc()).first()

    if mode is None:
        if last_row:
            last_date = last_row[0].date()
            gap = (today - last_date).days
            if gap > 30:
                return {"need_choice": True, "last_date": last_date.isoformat()}
            if gap <= 0:
                return {"ok": True, "synced_days": 0}
            # Start from last_date to refresh that day's data, then load new days
            date_from = last_date
        else:
            return {"need_choice": True, "last_date": None}
    elif mode == "month_begin":
        date_from = date(today.year, today.month, 1)
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


@app.get("/api/avito/groups", response_model=List[AvitoGroupResponse])
def avito_get_groups(db: Session = Depends(get_db)):
    return db.query(AvitoGroup).order_by(AvitoGroup.sort_order).all()


@app.post("/api/avito/groups", response_model=AvitoGroupResponse)
def avito_create_group(data: AvitoGroupCreate, db: Session = Depends(get_db)):
    max_order = db.query(func.max(AvitoGroup.sort_order)).scalar() or 0
    group = AvitoGroup(name=data.name, sort_order=max_order + 1)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@app.put("/api/avito/groups/{group_id}", response_model=AvitoGroupResponse)
def avito_update_group(group_id: int, data: AvitoGroupUpdate, db: Session = Depends(get_db)):
    group = db.query(AvitoGroup).filter(AvitoGroup.id == group_id).first()
    if not group:
        raise HTTPException(404, "Группа не найдена")
    group.name = data.name
    db.commit()
    db.refresh(group)
    return group


@app.delete("/api/avito/groups/{group_id}")
def avito_delete_group(group_id: int, move_to: int = Query(default=None), db: Session = Depends(get_db)):
    group = db.query(AvitoGroup).filter(AvitoGroup.id == group_id).first()
    if not group:
        raise HTTPException(404, "Группа не найдена")
    if move_to is not None:
        target = db.query(AvitoGroup).filter(AvitoGroup.id == move_to).first()
        if not target:
            raise HTTPException(404, "Целевая группа не найдена")
    else:
        target = db.query(AvitoGroup).filter(AvitoGroup.name == "Неопределено").first()
        if target and target.id == group_id:
            raise HTTPException(400, "Нельзя удалить группу Неопределено без целевой группы")
        move_to = target.id if target else None
    if move_to:
        db.query(AvitoItem).filter(AvitoItem.group_id == group_id).update({"group_id": move_to})
    db.delete(group)
    db.commit()
    return {"ok": True}


@app.post("/api/avito/items/move-group")
def avito_move_items(data: MoveItemsRequest, db: Session = Depends(get_db)):
    target = db.query(AvitoGroup).filter(AvitoGroup.id == data.group_id).first()
    if not target:
        raise HTTPException(404, "Группа не найдена")
    db.query(AvitoItem).filter(AvitoItem.avito_item_id.in_(data.item_ids)).update(
        {"group_id": data.group_id}
    )
    db.commit()
    return {"ok": True, "moved": len(data.item_ids)}


@app.get("/api/avito/groups-stats", response_model=List[AvitoGroupStats])
def avito_groups_stats(date_from: str = Query(...), date_to: str = Query(...), db: Session = Depends(get_db)):
    df = date.fromisoformat(date_from[:10])
    dt = date.fromisoformat(date_to[:10])
    df_dt = datetime.combine(df, datetime.min.time())
    dt_dt = datetime.combine(dt, datetime.min.time())
    groups = db.query(AvitoGroup).order_by(AvitoGroup.sort_order).all()
    result = []
    for g in groups:
        item_ids = [i.avito_item_id for i in db.query(AvitoItem).filter(AvitoItem.group_id == g.id).all()]
        if not item_ids:
            result.append(AvitoGroupStats(id=g.id, name=g.name, item_count=0))
            continue
        row = db.query(
            func.sum(AvitoItemDailyStat.impressions).label("impressions"),
            func.sum(AvitoItemDailyStat.views).label("views"),
            func.sum(AvitoItemDailyStat.contacts).label("contacts"),
            func.sum(AvitoItemDailyStat.favorites).label("favorites"),
            func.sum(AvitoItemDailyStat.spent).label("spent"),
        ).filter(
            AvitoItemDailyStat.avito_item_id.in_(item_ids),
            AvitoItemDailyStat.date >= df_dt,
            AvitoItemDailyStat.date <= dt_dt,
        ).first()
        result.append(AvitoGroupStats(
            id=g.id, name=g.name, item_count=len(item_ids),
            impressions=row.impressions if row else None, views=row.views if row else None,
            contacts=row.contacts if row else None, favorites=row.favorites if row else None,
            spent=row.spent if row else None,
        ))
    return result


def _build_avito_item_responses(items, date_from, date_to, db):
    df = date.fromisoformat(date_from[:10])
    dt = date.fromisoformat(date_to[:10])
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
        # Stage stats: ordered by pipeline with conversion between stages (excl. Отказ)
        all_pipeline_stages = db.query(PipelineStage).order_by(PipelineStage.order).all()
        stage_map = {s.id: s for s in all_pipeline_stages}
        clients_for_item = db.query(Client).join(AvitoChat, AvitoChat.client_id == Client.id
        ).filter(AvitoChat.avito_item_id == it.avito_item_id).all()
        stage_counts = {}
        for c in clients_for_item:
            passed = set(json.loads(c.stages_passed or "[]"))
            if not passed and c.stage_id:
                passed = {c.stage_id}
            for sid in passed:
                s = stage_map.get(sid)
                if s:
                    stage_counts[s.name] = stage_counts.get(s.name, 0) + 1
        ordered_stats = []
        prev_count = None
        for s in all_pipeline_stages:
            if s.name == "Отказ":
                continue
            count = stage_counts.get(s.name, 0)
            conv = None
            if prev_count is not None and prev_count > 0:
                conv = round(count / prev_count * 100, 1)
            ordered_stats.append(StageStat(
                stage_name=s.name, color=s.color, count=count, conversion_pct=conv,
            ))
            prev_count = count
        result.append(AvitoItemResponse(
            avito_item_id=it.avito_item_id,
            group_id=it.group_id,
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
            stage_stats=ordered_stats,
            is_active=it.is_active if it.is_active is not None else True,
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


@app.post("/api/avito/client/{client_id}/mark-read")
def avito_mark_read(client_id: int, db: Session = Depends(get_db)):
    """Mark all Avito messages as read for this client across all chats."""
    avito_chats = db.query(AvitoChat).filter(AvitoChat.client_id == client_id).all()
    if not avito_chats:
        raise HTTPException(404, "Чат Авито не найден")
    token = db.query(AvitoToken).first()
    now_dt = datetime.now().replace(microsecond=0)
    for avito_chat in avito_chats:
        if token:
            try:
                c = _make_avito_client(db)
                if c:
                    user_id = _get_avito_user_id(token)
                    if user_id:
                        c.mark_chat_as_read(user_id=int(user_id), chat_id=avito_chat.chat_id)
                        _save_avito_token(db, c)
            except Exception as e:
                print(f"Avito sync warning: {e}")  # non-fatal, still mark locally
        db.query(AvitoMessage).filter(
            AvitoMessage.chat_id == avito_chat.chat_id,
            AvitoMessage.is_read == False,
            AvitoMessage.author_name != "Вы",
        ).update({"is_read": True, "read_at": now_dt})
        avito_chat.unread_count = 0
    db.commit()
    return {"ok": True}


@app.post("/api/avito/client/{client_id}/sync-messages")
def avito_sync_client_messages(client_id: int, db: Session = Depends(get_db)):
    """Sync Avito messages for all client's chats, returning total imported count."""
    chats = db.query(AvitoChat).filter(AvitoChat.client_id == client_id).all()
    if not chats:
        raise HTTPException(404, "Чат Авито не найден")
    c = _make_avito_client(db)
    if not c:
        raise HTTPException(400, "Avito не подключён")
    token = db.query(AvitoToken).first()
    if not token:
        raise HTTPException(400, "Avito не подключён")
    imported = 0
    for chat in chats:
        try:
            msgs = c.get_messages(user_id=int(token.user_id), chat_id=chat.chat_id, limit=100)
            _save_avito_token(db, c)
            for m in msgs.messages:
                mid = str(m.id)
                if not mid:
                    continue
                existing_msg = db.query(AvitoMessage).filter(AvitoMessage.message_id == mid).first()
                if existing_msg:
                    updated = False
                    api_read = bool(m.is_read) if m.is_read else False
                    if not api_read and m.read:
                        api_read = True
                    if api_read != existing_msg.is_read:
                        existing_msg.is_read = api_read
                        if api_read and m.read:
                            try:
                                existing_msg.read_at = datetime.fromtimestamp(m.read)
                            except Exception:
                                existing_msg.read_at = datetime.now()
                        updated = True
                    is_ours = str(m.author_id) == str(token.user_id) if m.author_id else False
                    is_system = str(m.type) == 'system'
                    if is_system and existing_msg.author_name:
                        existing_msg.author_name = ""
                        updated = True
                    elif not is_system and not existing_msg.author_name:
                        existing_msg.author_name = "Вы" if is_ours else "Клиент"
                        updated = True
                    if not existing_msg.payload:
                        existing_msg.payload = str(m.type)
                        updated = True
                    if updated:
                        db.commit()
                    continue
                is_ours = str(m.author_id) == str(token.user_id) if m.author_id else False
                is_system = str(m.type) == 'system'
                if is_system:
                    author_name = ""
                elif is_ours:
                    author_name = "Вы"
                else:
                    author_name = "Клиент"
                msg = AvitoMessage(
                    chat_id=chat.chat_id, message_id=mid,
                    author_id=str(m.author_id) if m.author_id else "",
                    author_name=author_name,
                    content=(m.content.text or "") if m.content else "",
                    payload=str(m.type),
                    is_read=bool(m.is_read) if (m.is_read or m.read) else False,
                )
                try:
                    msg.created_at = datetime.fromtimestamp(m.created)
                except Exception:
                    msg.created_at = datetime.now()
                if m.read:
                    try:
                        msg.read_at = datetime.fromtimestamp(m.read)
                    except Exception as e:
                        print(f"Avito sync warning: {e}")
                db.add(msg)
                imported += 1
            db.commit()
        except Exception as e:
            print(f"Avito message sync error for chat {chat.chat_id}: {e}")
    return {"imported": imported}


@app.get("/api/avito/client/{client_id}/chat")
def avito_client_chat(client_id: int, db: Session = Depends(get_db)):
    chats = db.query(AvitoChat).filter(AvitoChat.client_id == client_id).order_by(AvitoChat.id).all()
    if not chats:
        return None
    chat = next((c for c in chats if c.item_url), chats[0])
    address = ""
    if chat.avito_item_id:
        item = db.query(AvitoItem).filter(AvitoItem.avito_item_id == chat.avito_item_id).first()
        if item:
            address = item.address or ""
    return {"chat_id": chat.chat_id, "item_title": chat.item_title, "item_url": chat.item_url, "item_image": chat.item_image, "address": address}


# ---- Quick replies API ----
@app.get("/api/quick-replies")
def get_quick_replies():
    cfg = _load_config()
    return cfg.get("quick_replies", [])

@app.put("/api/quick-replies")
def save_quick_replies(data: dict = Body(...)):
    cfg = _load_config()
    cfg["quick_replies"] = data.get("replies", [])
    _save_config(cfg)
    return {"ok": True}

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


# ---- Notifications ----

def _create_notification(db: Session, type: str, message: str, link_id: int = None):
    n = Notification(type=type, message=message, link_id=link_id)
    db.add(n)
    db.commit()


@app.get("/api/notifications", response_model=NotificationsResponse)
def get_notifications(feed: bool = Query(default=False), db: Session = Depends(get_db)):
    from datetime import timedelta
    now = datetime.now().replace(microsecond=0)
    week_ago = now - timedelta(days=7)
    items = []

    if feed:
        all_items = db.query(Notification).filter(
            Notification.created_at >= week_ago,
        ).order_by(Notification.created_at.asc()).limit(50).all()
        for n in all_items:
            if n.link_id:
                client = db.query(Client).filter(Client.id == n.link_id).first()
                if not client:
                    continue
            items.append(NotificationItem(
                id=n.id, type=n.type, message=n.message, link_id=n.link_id,
                time=n.created_at.isoformat()[:19] if n.created_at else None,
            ))
        return NotificationsResponse(total=len(items), items=items)

    # Bell: merge notification table + live Avito unread
    reject_stage = db.query(PipelineStage).filter(PipelineStage.name == "Отказ").first()
    reject_stage_id = reject_stage.id if reject_stage else -1
    notif_ids_seen = set()
    # 1. Unread from notifications table
    unread_notifs = db.query(Notification).filter(Notification.is_read == False).order_by(Notification.created_at.desc()).all()
    for n in unread_notifs:
        if n.link_id:
            client = db.query(Client).filter(Client.id == n.link_id).first()
            if not client:
                n.is_read = True
                db.commit()
                continue
            if client.stage_id == reject_stage_id:
                continue
        notif_ids_seen.add((n.type, n.link_id))
        items.append(NotificationItem(
            id=n.id, type=n.type, message=n.message, link_id=n.link_id,
            time=n.created_at.isoformat()[:19] if n.created_at else None,
        ))

    # 2. Unread Avito chats not yet in notifications table — show as live items
    chats = db.query(AvitoChat).filter(
        AvitoChat.unread_count > 0,
        AvitoChat.client_id.isnot(None),
    ).order_by(AvitoChat.last_message_at.desc()).all()
    for c in chats:
        link = c.client_id
        if ('avito_message', link) in notif_ids_seen or not c.client or c.client.stage_id == reject_stage_id:
            continue
        deal_name = c.client.deal_name or ""
        client_name = c.client.name or "Клиент"
        last_unread = db.query(AvitoMessage.content).filter(
            AvitoMessage.chat_id == c.chat_id,
            AvitoMessage.is_read == False,
            AvitoMessage.author_name != "Вы",
            AvitoMessage.content != "",
        ).order_by(AvitoMessage.created_at.desc()).first()
        parts = [s for s in [deal_name, client_name] if s]
        msg = f"Авито: {', '.join(parts)}"
        if c.unread_count > 1:
            msg += f" (+{c.unread_count - 1})"
        if last_unread and last_unread[0]:
            txt = last_unread[0][:100]
            if len(last_unread[0]) > 100:
                txt += "..."
            msg += f" — {txt}"
        items.append(NotificationItem(
            id=0, type="avito_message", message=msg,
            link_id=link, time=c.last_message_at.isoformat()[:19] if c.last_message_at else None,
        ))

    # 3. Overdue tasks — show as live items
    today_start = datetime.combine(datetime.now().date(), datetime.min.time())
    tomorrow_start = today_start + timedelta(days=1)
    day_after_start = today_start + timedelta(days=2)
    overdue_tasks = db.query(Task).filter(
        Task.completed == False,
        Task.due_date != None,
        Task.due_date < today_start,
        Task.client_id.isnot(None),
    ).order_by(Task.due_date.asc()).all()
    seen_overdue = set()
    overdue_added = 0
    for t in overdue_tasks:
        if t.client_id in seen_overdue:
            continue
        seen_overdue.add(t.client_id)
        client = db.query(Client).filter(Client.id == t.client_id).first()
        if not client or client.stage_id == reject_stage_id:
            continue
        deal_name = client.deal_name or client.name or "Клиент"
        cnt = db.query(Task).filter(
            Task.client_id == t.client_id,
            Task.completed == False,
            Task.due_date != None,
            Task.due_date < today_start,
        ).count()
        msg = f"Просрочена задача: {deal_name}"
        if cnt > 1:
            msg += f" (+{cnt - 1})"
        overdue_added += 1
        items.append(NotificationItem(
            id=0, type="overdue_task", message=msg,
            link_id=t.client_id,
            time=t.due_date.isoformat()[:19] if t.due_date else None,
        ))

    # 4. Tasks due today — show as live items
    today_tasks = db.query(Task).filter(
        Task.completed == False,
        Task.due_date != None,
        Task.due_date >= today_start,
        Task.due_date < tomorrow_start,
        Task.client_id.isnot(None),
    ).order_by(Task.due_date.asc()).all()
    seen_today = set()
    today_added = 0
    for t in today_tasks:
        if t.client_id in seen_today:
            continue
        seen_today.add(t.client_id)
        client = db.query(Client).filter(Client.id == t.client_id).first()
        if not client or client.stage_id == reject_stage_id:
            continue
        deal_name = client.deal_name or client.name or "Клиент"
        cnt = db.query(Task).filter(
            Task.client_id == t.client_id,
            Task.completed == False,
            Task.due_date != None,
            Task.due_date >= today_start,
            Task.due_date < tomorrow_start,
        ).count()
        msg = f"Задача на сегодня: {deal_name}"
        if cnt > 1:
            msg += f" (+{cnt - 1})"
        today_added += 1
        items.append(NotificationItem(
            id=0, type="today_task", message=msg,
            link_id=t.client_id,
            time=t.due_date.isoformat()[:19] if t.due_date else None,
        ))

    # 5. Tasks due tomorrow — show as live items
    tomorrow_tasks = db.query(Task).filter(
        Task.completed == False,
        Task.due_date != None,
        Task.due_date >= tomorrow_start,
        Task.due_date < day_after_start,
        Task.client_id.isnot(None),
    ).order_by(Task.due_date.asc()).all()
    seen_tomorrow = set()
    tomorrow_task_clients = set()
    for t in tomorrow_tasks:
        if t.client_id in seen_tomorrow:
            continue
        seen_tomorrow.add(t.client_id)
        client = db.query(Client).filter(Client.id == t.client_id).first()
        if not client or client.stage_id == reject_stage_id:
            continue
        tomorrow_task_clients.add(t.client_id)
    tomorrow_added = 0
    if tomorrow_task_clients:
        tomorrow_added = 1
        items.append(NotificationItem(
            id=0, type="tomorrow_task", message=f"Задачи на завтра ({len(tomorrow_task_clients)})",
            link_id=0,
            time=None,
        ))

    # 6. Deals with no tasks — single aggregated item
    no_task_count = db.query(Client).filter(
        ~Client.tasks.any(),
        Client.stage_id != reject_stage_id,
    ).count()
    no_task_added = 0
    if no_task_count > 0:
        msg = f"Нет задач ({no_task_count})"
        no_task_added = 1
        items.append(NotificationItem(
            id=0, type="no_task", message=msg,
            link_id=0,
            time=None,
        ))

    total = sum(c.unread_count for c in chats)
    total += db.query(Notification).filter(
        Notification.is_read == False,
        Notification.type != "avito_message",
    ).count()
    total += overdue_added + today_added + tomorrow_added + no_task_added
    return NotificationsResponse(total=total, items=items)


@app.post("/api/notifications/{notif_id}/read")
def mark_notification_read(notif_id: int, db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == notif_id).first()
    if n:
        n.is_read = True
        db.commit()
    return {"ok": True}


@app.post("/api/notifications/read-all")
def mark_all_notifications_read(db: Session = Depends(get_db)):
    db.query(Notification).filter(Notification.is_read == False).update({"is_read": True})
    # Also mark all Avito messages and chats as read
    now_dt = datetime.now().replace(microsecond=0)
    token = db.query(AvitoToken).first()
    if token:
        try:
            c = _make_avito_client(db)
            if c:
                user_id = _get_avito_user_id(token)
                if user_id:
                    chats = db.query(AvitoChat).filter(AvitoChat.unread_count > 0).all()
                    for avito_chat in chats:
                        try:
                            c.mark_chat_as_read(user_id=int(user_id), chat_id=avito_chat.chat_id)
                        except Exception as e:
                            print(f"Avito sync warning: {e}")
                    _save_avito_token(db, c)
        except Exception as e:
            print(f"Avito sync warning: {e}")
    db.query(AvitoMessage).filter(AvitoMessage.is_read == False, AvitoMessage.author_name != "Вы").update({"is_read": True, "read_at": now_dt})
    db.query(AvitoChat).update({"unread_count": 0})
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
