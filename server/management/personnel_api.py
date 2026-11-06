"""Personnel routes installed explicitly by a future runtime upgrade."""
from pathlib import Path
from typing import Annotated,Literal

from fastapi import Depends,Header,Query
from fastapi.responses import FileResponse
from pydantic import Field
from business.models import Input
from business.store import Problem
from .personnel import PersonnelStore


class PersonnelPagePolicy:
    """Apply the same strict self-only policy as the existing workspace page."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('path')!='/people':return await self.app(scope,receive,send)
        async def outgoing(message):
            if message['type']=='http.response.start':
                message['headers']=[(k,v) for k,v in message.get('headers',[]) if k.lower()!=b'content-security-policy']+[(b'content-security-policy',b"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")]
            await send(message)
        await self.app(scope,receive,outgoing)


class PersonInput(Input):
    workspace_role: Literal['admin']
    login: str=Field(min_length=3,max_length=64)
    display_name: str=Field(min_length=1,max_length=80)
    roles: list[Literal['admin','teacher','principal','assessor']]=Field(min_length=1,max_length=4)
    active: bool=True
    approval_class_ids: list[str]=Field(default_factory=list,max_length=1000)
    password: str|None=Field(default=None,min_length=6,max_length=128,repr=False)
    expected_revision: int|None=Field(default=None,ge=0)
    reason: str=Field(min_length=1,max_length=200)
    command_ref: str=Field(min_length=1,max_length=80)


def install_personnel_routes(app,database,clock=None):
    existing=getattr(app.state,'personnel_store',None)
    if existing is not None:
        if existing.path.resolve()!=Path(database).resolve():raise ValueError('Personnel database differs')
        return existing
    store=PersonnelStore(database,clock)
    with store.transaction() as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if not {'management_staff_versions','management_staff_commands'}<=tables:raise ValueError('Explicit personnel migration required')
    app.state.personnel_store=store
    app.add_middleware(PersonnelPagePolicy)
    def authorization(value:Annotated[str|None,Header(alias='Authorization')]=None):
        if not value or not value.startswith('Bearer ') or len(value)>128:raise Problem(401,'unauthorized','请先登录')
        return value[7:]
    Token=Annotated[str,Depends(authorization)]
    assets=Path(__file__).parent/'web'
    @app.get('/people')
    def people_page():return FileResponse(assets/'personnel.html',media_type='text/html')
    @app.get('/personnel.js')
    def people_script():return FileResponse(assets/'personnel.js',media_type='application/javascript')
    @app.get('/v1/management/people')
    def people(token:Token,workspace_role:Literal['admin'],q:Annotated[str,Query(max_length=80)]='',
               before:Annotated[str|None,Query(max_length=80)]=None,limit:Annotated[int,Query(ge=1,le=100)]=50):
        return store.staff_list(token,workspace_role,q=q,before=before,limit=limit)
    @app.post('/v1/management/people',status_code=201)
    def create_person(data:PersonInput,token:Token):
        return store.staff_save(token,data.workspace_role,**data.model_dump(exclude={'workspace_role'}))
    @app.post('/v1/management/people/{user_id}')
    def update_person(user_id:str,data:PersonInput,token:Token):
        return store.staff_save(token,data.workspace_role,user_id=user_id,**data.model_dump(exclude={'workspace_role'}))
    return store
