"""SQLite项目目录及不可变快照。数据库只保存索引，较大的状态保存在版本文件中。"""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
import os
from pathlib import Path
import re
import sqlite3
import uuid

from . import PROJECT_ROOT
from .project_codec import ProjectError, dump_project, load_project, STATE_FIELDS


class ProjectConflict(ProjectError):
    pass


class ProjectMissing(ProjectError):
    pass


def project_home():
    return Path(os.environ.get('WQ_PROJECT_HOME',str(Path(PROJECT_ROOT)/'data'/'projects'))).resolve()


def runtime_directory():
    root=project_home()/'runtime'/uuid.uuid4().hex
    root.mkdir(parents=True,exist_ok=True)
    return str(root)


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{32}',value):
        raise ProjectMissing('项目不存在')
    return value


def _name(value):
    name=value.strip()
    if not 1<=len(name)<=100:
        raise ProjectError('项目名称须为1～100个字符')
    return name


class ProjectRepository:
    def __init__(self,root):
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.database=self.root/'projects.sqlite3'
        with self.connection() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] not in (0,1):
                raise ProjectError('不支持的项目数据库版本，请使用匹配版本程序')
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL,
                    snapshot TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (
                    project_id TEXT NOT NULL REFERENCES projects(id), revision INTEGER NOT NULL,
                    snapshot TEXT NOT NULL, saved_at TEXT NOT NULL,
                    PRIMARY KEY(project_id,revision));
                CREATE TABLE IF NOT EXISTS bindings (
                    session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id));
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.database,timeout=15)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute(
                'SELECT id,name,revision,created_at,updated_at FROM projects ORDER BY updated_at DESC,id')]

    def info(self,project_id):
        with self.connection() as db:
            row=db.execute('SELECT * FROM projects WHERE id=?',(_id(project_id),)).fetchone()
        if row is None:
            raise ProjectMissing('项目不存在')
        return dict(row)

    def raw(self,project_id):
        info=self.info(project_id)
        relative=info['snapshot']
        if not re.fullmatch(r'[0-9a-f]{32}/[0-9a-f]{32}\.wqproj',relative):
            raise ProjectError('项目存储路径无效')
        try:
            raw=(self.root/relative).read_bytes()
        except OSError as exc:
            raise ProjectError('项目快照无法读取，请从导出备份恢复') from exc
        return info,raw

    def read(self,project_id):
        info,raw=self.raw(project_id)
        return info,load_project(raw)

    def write(self,session,name=None,new=False):
        project_id=uuid.uuid4().hex if new else _id(session.project_id)
        old=None if new else self.info(project_id)
        expected=session.project_revision
        name=_name(name if name is not None else old['name'])
        raw=dump_project(session,name)
        # 在发布前回读校验；故障不会替换数据库中的旧快照。
        name=load_project(raw).name
        now=datetime.now(timezone.utc).isoformat()
        relative=f'{project_id}/{uuid.uuid4().hex}.wqproj'
        path=self.root/relative
        path.parent.mkdir(exist_ok=True)
        temporary=path.with_suffix('.tmp')
        try:
            with temporary.open('wb') as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary,path)
            revision=1 if new else expected+1
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                if new:
                    db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?)',
                        (project_id,name,revision,relative,now,now))
                else:
                    changed=db.execute('UPDATE projects SET name=?,revision=?,snapshot=?,updated_at=? WHERE id=? AND revision=?',
                        (name,revision,relative,now,project_id,expected)).rowcount
                    if changed!=1:
                        raise ProjectConflict('项目已在另一个会话中更新，请另存为新项目以保留当前修改')
                db.execute('INSERT INTO revisions VALUES (?,?,?,?)',(project_id,revision,relative,now))
                db.execute('INSERT INTO bindings VALUES (?,?) ON CONFLICT(session_id) DO UPDATE SET project_id=excluded.project_id',
                    (session.id,project_id))
        except Exception:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise
        session.project_id=project_id
        session.project_revision=revision
        session.restore_error=''
        return self.info(project_id)

    def bind(self,session_id,project_id):
        with self.connection() as db:
            db.execute('INSERT INTO bindings VALUES (?,?) ON CONFLICT(session_id) DO UPDATE SET project_id=excluded.project_id',
                (session_id,project_id))

    def restore(self,session_id):
        from .session import Session
        with self.connection() as db:
            row=db.execute('SELECT project_id FROM bindings WHERE session_id=?',(session_id,)).fetchone()
        if row is None:
            return None
        info,snapshot=self.read(row['project_id'])
        session=Session(id=session_id)
        apply_snapshot(session,info,snapshot)
        return session


def apply_snapshot(session,info,snapshot):
    # 先物化到独立目录，再替换会话字段，失败时旧内存状态保持完整。
    directory=Path(runtime_directory())
    for filename,raw in snapshot.artifacts.items():
        (directory/filename).write_bytes(raw)
    def relocate(value):
        if isinstance(value,str) and value.startswith('artifacts/'):
            filename=value[len('artifacts/'):]
            if filename in snapshot.artifacts:
                return str(directory/filename)
        if isinstance(value,dict):
            return {k:relocate(v) for k,v in value.items()}
        if isinstance(value,list):
            return [relocate(v) for v in value]
        return value
    revision=session.analysis_revision
    restored={k:relocate(v) for k,v in snapshot.state.items()}
    for key in STATE_FIELDS:
        setattr(session,key,restored[key])
    session._media_bytes=dict(getattr(snapshot,'media',{}) or {})
    session._vision_image_cache={}
    session.analysis_revision=max(revision,session.analysis_revision)+1
    session.project_id=info['id']
    session.project_revision=info['revision']
    session.restore_error=''
    session.agent_run=None
    session._side_run=None
    session._side_thread_id=None
    session._side_save_notice=''
    if session.river_state:
        session.river_state.output_dir=str(directory)
    if session.analysis_status=='running':
        session.analysis_status='cancelled'
        session.analysis_done=False


@lru_cache(maxsize=8)
def _repository(path):
    return ProjectRepository(path)


def repository():
    return _repository(str(project_home()))


def restore_saved_session(session_id):
    # 未使用项目功能时保持轻量的临时会话，不因普通健康检查创建数据库。
    if not (project_home()/'projects.sqlite3').exists():
        return None
    return repository().restore(session_id)
