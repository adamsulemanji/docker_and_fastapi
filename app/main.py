from typing import List, Optional
from datetime import datetime, timedelta
from enum import Enum
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, validator
import uuid

app = FastAPI()

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"

class TaskBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    priority: TaskPriority = Field(TaskPriority.MEDIUM)
    estimated_hours: Optional[float] = Field(None, ge=0.1, le=100)
    due_date: Optional[datetime] = Field(None)

class TaskCreate(TaskBase):
    @validator('due_date')
    def due_date_must_be_future(cls, v):
        if v and v <= datetime.now():
            raise ValueError('Due date must be in the future')
        return v

class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    priority: Optional[TaskPriority] = None
    status: Optional[TaskStatus] = None
    estimated_hours: Optional[float] = Field(None, ge=0.1, le=100)
    due_date: Optional[datetime] = None
    actual_hours: Optional[float] = Field(None, ge=0, le=200)

    @validator('due_date')
    def due_date_validation(cls, v):
        if v and v <= datetime.now():
            raise ValueError('Due date must be in the future')
        return v

class Task(TaskBase):
    id: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime
    updated_at: datetime
    actual_hours: Optional[float] = None
    is_overdue: bool = False

    class Config:
        use_enum_values = True

tasks_db = {}

def calculate_overdue_status(task_data: dict) -> bool:
    if task_data.get('due_date') and task_data['status'] != TaskStatus.COMPLETED:
        return datetime.now() > task_data['due_date']
    return False

def apply_business_rules(task_data: dict) -> dict:
    if task_data['status'] == TaskStatus.COMPLETED and not task_data.get('actual_hours'):
        task_data['actual_hours'] = task_data.get('estimated_hours', 1.0)
    
    if task_data['priority'] == TaskPriority.URGENT and not task_data.get('due_date'):
        task_data['due_date'] = datetime.now() + timedelta(days=1)
    
    task_data['is_overdue'] = calculate_overdue_status(task_data)
    task_data['updated_at'] = datetime.now()
    
    return task_data

@app.post("/tasks", response_model=Task, status_code=201)
def create_task(task: TaskCreate):
    task_id = str(uuid.uuid4())
    task_data = task.dict()
    task_data.update({
        'id': task_id,
        'status': TaskStatus.PENDING,
        'created_at': datetime.now(),
        'updated_at': datetime.now(),
        'actual_hours': None,
        'is_overdue': False
    })
    
    task_data = apply_business_rules(task_data)
    tasks_db[task_id] = task_data
    return Task(**task_data)

@app.get("/tasks", response_model=List[Task])
def read_all_tasks(
    status: Optional[TaskStatus] = Query(None, description="Filter by status"),
    priority: Optional[TaskPriority] = Query(None),
    overdue_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=1000)
):
    filtered_tasks = []
    
    for task_data in tasks_db.values():
        task_data['is_overdue'] = calculate_overdue_status(task_data)
        
        if status and task_data['status'] != status:
            continue
        if priority and task_data['priority'] != priority:
            continue
        if overdue_only and not task_data['is_overdue']:
            continue
            
        filtered_tasks.append(Task(**task_data))
    
    filtered_tasks.sort(key=lambda x: (x.priority == TaskPriority.URGENT, x.created_at), reverse=True)
    return filtered_tasks[:limit]

@app.get("/tasks/{task_id}", response_model=Task)
def read_task(task_id: str):
    if task_id not in tasks_db: 
        raise HTTPException(status_code=404, detail="Task not found")
    
    task_data = tasks_db[task_id].copy()
    task_data['is_overdue'] = calculate_overdue_status(task_data)
    return Task(**task_data)

@app.put("/tasks/{task_id}", response_model=Task)
def update_task(task_id: str, task_update: TaskUpdate):
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task_data = tasks_db[task_id].copy()
    
    if task_data['status'] == TaskStatus.COMPLETED and task_update.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Cannot change status of completed task")
    
    if task_update.status == TaskStatus.COMPLETED and task_data['status'] != TaskStatus.COMPLETED:
        if not task_update.actual_hours and not task_data.get('actual_hours'):
            task_update.actual_hours = task_data.get('estimated_hours', 1.0)
    
    update_data = task_update.dict(exclude_unset=True)
    task_data.update(update_data)
    task_data = apply_business_rules(task_data)
    
    tasks_db[task_id] = task_data
    return Task(**task_data)

@app.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks_db[task_id]
    if task['status'] == TaskStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="Cannot delete task in progress")
    
    del tasks_db[task_id]
    return {"message": "Task deleted successfully"}

@app.delete("/tasks")
def delete_all_tasks(force: bool = Query(False, description="Force delete all tasks including in-progress ones")):
    if not force:
        in_progress_count = sum(1 for task in tasks_db.values() if task['status'] == TaskStatus.IN_PROGRESS)
        if in_progress_count > 0:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot delete {in_progress_count} in-progress tasks. Use force=true to override"
            )
    
    deleted_count = len(tasks_db)
    tasks_db.clear()
    return {"message": f"Deleted {deleted_count} tasks successfully"}
