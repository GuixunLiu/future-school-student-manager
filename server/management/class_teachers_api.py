"""Explicitly migrated class administration and fixed device metadata routes."""
from pathlib import Path
from typing import Annotated,Literal
from fastapi import Depends,Header,Query
from fastapi.responses import FileResponse
from pydantic import Field
from business.models import Input
from business.store import Problem
from .class_teachers import ClassTeacherStore


class TeacherAssignment(Input):
    workspace_role: Literal['admin']
    teacher_id: str=Field(min_length=1,max_length=80)
    expected_revision: int=Field(ge=0)
    reason: str=Field(min_length=1,max_length=200)
    command_ref: str=Field(min_length=1,max_length=80)


class MetadataReady(Input):
    generation: str=Field(min_length=1,max_length=80)


class MetadataReceipt(MetadataReady):
    class_id: str=Field(min_length=1,max_length=80)
    revision: int=Field(ge=1)
    receipt_token: str=Field(pattern='^[a-f0-9]{32}$')
    sha256: str=Field(pattern='^[a-f0-9]{64}$')
    status: Literal['applied','conflict']


def install_class_teacher_routes(app,database,clock=None):
    store=ClassTeacherStore(database,clock)
    with store.transaction() as db:
        if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_class_teacher_events'").fetchone():raise ValueError('Explicit class teacher migration required')
    app.state.class_teacher_store=store
    def auth(value:Annotated[str|None,Header(alias='Authorization')]=None):
        if not value or not value.startswith('Bearer ') or len(value)>128:raise Problem(401,'unauthorized','请先登录')
        return value[7:]
    Token=Annotated[str,Depends(auth)]
    @app.get('/class-teachers.js')
    def script():return FileResponse(Path(__file__).parent/'web/class-teachers.js',media_type='application/javascript')
    @app.get('/v1/management/classes')
    def classes(token:Token,workspace_role:Literal['admin','principal'],q:Annotated[str,Query(max_length=80)]='',before:Annotated[str|None,Query(max_length=80)]=None,limit:Annotated[int,Query(ge=1,le=100)]=50):
        return store.class_list(token,workspace_role,q=q,before=before,limit=limit)
    @app.post('/v1/management/classes/{class_id}/teacher')
    def assign(class_id:str,data:TeacherAssignment,token:Token):return store.assign_teacher(token,data.workspace_role,class_id=class_id,**data.model_dump(exclude={'workspace_role'}))
    @app.post('/v1/management/device/class-ready')
    def ready(data:MetadataReady,token:Token):return store.metadata_ready(token,data.generation)
    @app.get('/v1/management/device/class-next')
    def next_event(token:Token,generation:Annotated[str,Query(min_length=1,max_length=80)],
                   defer:Annotated[str,Query(max_length=3239)]=''):
        return store.metadata_next(token,generation,defer)
    @app.post('/v1/management/device/class-receipt')
    def receipt(data:MetadataReceipt,token:Token):return store.metadata_ack(token,**data.model_dump())
    return store
