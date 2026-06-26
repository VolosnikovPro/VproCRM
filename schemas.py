from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class StageBase(BaseModel):
    name: str
    order: int
    color: str = "#6b7280"


class StageCreate(StageBase):
    pass


class StageResponse(StageBase):
    id: int
    client_count: int = 0
    total_budget: int = 0

    class Config:
        from_attributes = True


class SourceBase(BaseModel):
    name: str
    order: int = 0


class SourceCreate(SourceBase):
    pass


class SourceResponse(SourceBase):
    id: int

    class Config:
        from_attributes = True


class TagBase(BaseModel):
    name: str
    color: str = "#6b7280"


class TagCreate(TagBase):
    pass


class TagResponse(TagBase):
    id: int

    class Config:
        from_attributes = True


class NoteResponse(BaseModel):
    id: int
    client_id: int
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class NoteCreate(BaseModel):
    content: str


class NoteUpdate(BaseModel):
    content: str


class TaskResponse(BaseModel):
    id: int
    client_id: int
    client_name: str = ""
    title: str
    due_date: Optional[datetime] = None
    completed: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class TaskCreate(BaseModel):
    title: str
    due_date: Optional[datetime] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    due_date: Optional[datetime] = None
    completed: Optional[bool] = None


class ActivityLogResponse(BaseModel):
    id: int
    client_id: int
    action: str
    description: str
    created_at: datetime

    class Config:
        from_attributes = True


class CustomField(BaseModel):
    field_name: str
    field_value: str


class ClientBase(BaseModel):
    name: str
    deal_name: str = ""
    phone: str = ""
    email: str = ""
    organization: str = ""
    address: str = ""
    responsible: str = ""
    budget: int = 0
    source: str = ""
    stage_id: int


class ClientCreate(ClientBase):
    custom_fields: List[CustomField] = []
    tag_ids: List[int] = []


class ClientUpdate(BaseModel):
    name: Optional[str] = None
    deal_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    organization: Optional[str] = None
    address: Optional[str] = None
    responsible: Optional[str] = None
    budget: Optional[int] = None
    source: Optional[str] = None
    stage_id: Optional[int] = None
    rejection_reason_id: Optional[int] = None
    custom_fields: Optional[List[CustomField]] = None
    tag_ids: Optional[List[int]] = None


class ClientResponse(ClientBase):
    id: int
    created_at: datetime
    updated_at: datetime
    notes: List[NoteResponse] = []
    tasks: List[TaskResponse] = []
    custom_fields: List[CustomField] = []
    tags: List[TagResponse] = []
    activity_log: List[ActivityLogResponse] = []
    task_count: int = 0
    overdue_count: int = 0
    today_count: int = 0
    week_count: int = 0
    later_count: int = 0
    last_activity: Optional[datetime] = None

    class Config:
        from_attributes = True


class BatchMove(BaseModel):
    client_ids: List[int]
    stage_id: int


class BatchDelete(BaseModel):
    client_ids: List[int]


class ImportResult(BaseModel):
    imported: int
    errors: List[str]


class TaskListResponse(BaseModel):
    overdue: List[TaskResponse]
    today: List[TaskResponse]
    week: List[TaskResponse]
    later: List[TaskResponse]


class DashboardStats(BaseModel):
    total_clients: int
    stage_distribution: List[dict]
    source_distribution: List[dict]
    recent_clients: int
    total_tasks: int
    completed_tasks: int
    overdue_tasks: int
    no_task_clients: int
    total_budget: float
    avg_budget: float
    budget_by_stage: List[dict]
    by_responsible: List[dict]
    active_7d: int
    active_30d: int
    inactive_clients: int
    tag_distribution: List[dict]
    monthly_clients: List[dict]
    task_completion_by_stage: List[dict]


class AvitoChatResponse(BaseModel):
    id: int
    chat_id: str
    client_id: Optional[int] = None
    client_name: str = ""
    other_user_name: str
    other_user_phone: str = ""
    item_title: str
    item_url: str
    item_image: str = ""
    last_message_preview: str = ""
    last_message_at: Optional[datetime] = None
    unread_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class AvitoMessageResponse(BaseModel):
    id: int
    message_id: str
    author_id: str
    author_name: str
    content: str
    payload: str = ""
    is_read: bool = False
    read_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AvitoSendMessage(BaseModel):
    content: str


class StageStat(BaseModel):
    stage_name: str
    color: str
    count: int


class AvitoItemResponse(BaseModel):
    avito_item_id: Optional[int] = None
    title: str
    address: str
    url: str
    price: Optional[int] = None
    status: str
    category: str
    placed_at: Optional[datetime] = None
    impressions: Optional[int] = None
    views: Optional[int] = None
    contacts: Optional[int] = None
    favorites: Optional[int] = None
    spent: Optional[float] = None
    price_per_view: Optional[float] = None
    price_per_contact: Optional[float] = None
    stats_updated_at: Optional[datetime] = None
    stage_stats: List[StageStat] = []

    class Config:
        from_attributes = True


class SyncItemsResult(BaseModel):
    synced: int
    total: int


class RejectionReasonCreate(BaseModel):
    name: str


class RejectionReasonUpdate(BaseModel):
    name: str


class RejectionReasonResponse(BaseModel):
    id: int
    name: str
    order: int

    class Config:
        from_attributes = True
