"""可迁移项目包：JSON元数据及无pickle的NumPy数组；只恢复已知数据类型。"""
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import PurePosixPath, Path
import platform
import re
import zipfile
import zlib

import numpy as np
import pandas as pd

from river_model.config import RiverConfig
from river_model.cross_sections import CrossSection
from river_model.state import RiverModelState, HydroResult, WQResult

MAX_PACKAGE = 256 * 1024**2
MAX_EXPANDED = 512 * 1024**2
MODEL_FILES = {'river_hydro_profile.png', 'river_discharge_profile.png', 'river_wq_contour.png',
    'river_wq_profile.png', 'river_wq_timeseries.png', 'river_dashboard.png',
    'river_pollution_animation.html', 'river_model_export.json', 'river_model_export.csv'}
STATE_FIELDS = ('df', 'video_frames', 'data_filename', 'video_filename', 'black_spots',
    'black_spots_json', 'cross_validation', 'comprehensive_summary', 'national_station_result',
    'captions', 'tool_evidence', 'target_water_class', 'messages', 'full_messages',
    'analysis_done', 'analysis_status', 'analysis_steps', 'analysis_revision', 'river_state', 'river_ui')
CLASSES = {cls.__name__: cls for cls in (RiverConfig, CrossSection, RiverModelState, HydroResult, WQResult)}


class ProjectError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def _read_json(raw):
    def nonfinite(_):
        raise ProjectError('项目JSON包含非标准数值')
    return json.loads(raw,parse_constant=nonfinite)


def _scrubber():
    from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key
    secrets = [resolve_api_key(k) for k in ('DEEPSEEK_API_KEY', 'AMAP_KEY', 'CLOUD_TOKEN')]
    def scrub(text):
        for secret in secrets:
            if secret:
                text = text.replace(secret, '[已移除密钥]')
        return re.sub(r'\bsk-[A-Za-z0-9_-]{12,}', '[已移除密钥]', text)
    return scrub


def dump_project(session, name):
    entries = {}
    scrub = _scrubber()
    model_dir = getattr(session.river_state, 'output_dir', '') if session.river_state else ''
    if model_dir:
        for filename in MODEL_FILES:
            path = Path(model_dir) / filename
            if path.is_file() and not path.is_symlink():
                raw = path.read_bytes()
                if path.suffix in ('.html', '.json', '.csv'):
                    raw = scrub(raw.decode('utf-8')).encode('utf-8')
                entries[f'artifacts/{filename}'] = raw

    def pack(value, depth=0):
        if depth > 50:
            raise ProjectError('项目数据嵌套过深')
        child = lambda v: pack(v, depth+1)
        if value is None or value is pd.NA or value is pd.NaT:
            return None
        if isinstance(value, np.generic) and not isinstance(value, (np.datetime64, np.timedelta64)):
            value = value.item()
        if isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            return value if np.isfinite(value) else {'kind':'float', 'value':str(value)}
        if isinstance(value, str):
            if model_dir:
                value = value.replace(model_dir + '/', 'artifacts/').replace(model_dir + '\\', 'artifacts/')
            return scrub(value)
        if isinstance(value, (pd.Timestamp, datetime, np.datetime64)):
            return {'kind':'timestamp', 'value':pd.Timestamp(value).isoformat()}
        if isinstance(value, (pd.Timedelta, np.timedelta64)):
            return {'kind':'timedelta', 'value':pd.Timedelta(value).value}
        if isinstance(value, np.ndarray):
            if value.dtype.kind not in 'biufMm':
                return {'kind':'list', 'items':[child(v) for v in value.tolist()]}
            buffer = BytesIO()
            np.save(buffer, value, allow_pickle=False)
            key = f'arrays/{sum(k.startswith("arrays/") for k in entries):05d}.npy'
            entries[key] = buffer.getvalue()
            return {'kind':'array', 'file':key}
        if isinstance(value, pd.DataFrame):
            if isinstance(value.index, pd.MultiIndex) or not value.columns.is_unique:
                raise ProjectError('暂不支持多层索引或重复列名，请先整理监测表')
            columns = []
            for col in value.columns:
                series = value[col]
                vector = series.to_numpy()
                if vector.dtype.kind not in 'biufMm':
                    vector = series.tolist()
                if re.search(r'api.?key|token|secret|password', str(col), re.I):
                    vector = ['[已移除密钥]']*len(series)
                dtype='object' if re.search(r'api.?key|token|secret|password',str(col),re.I) else str(series.dtype)
                columns.append({'name':child(col), 'dtype':dtype, 'values':child(vector)})
            return {'kind':'dataframe', 'columns':columns, 'index':child(value.index.to_numpy()),
                    'index_name':child(value.index.name), 'attrs':child(value.attrs)}
        if is_dataclass(value) and type(value).__name__ in CLASSES:
            return {'kind':type(value).__name__, 'fields':{
                f.name:child(getattr(value,f.name)) for f in fields(value) if f.metadata.get('persist',True)}}
        if isinstance(value, dict):
            return {'kind':'mapping', 'items':[[child(k), '[已移除密钥]' if re.search(
                r'^(api.?key|amap_key|cloud_token|secret|password)$',str(k),re.I) else child(v)] for k,v in value.items()]}
        if isinstance(value, (tuple,list)):
            return {'kind':'tuple' if isinstance(value,tuple) else 'list', 'items':[child(v) for v in value]}
        raise ProjectError(f'不能保存的数据类型：{type(value).__name__}')

    entries['state.json'] = _json(pack({key:getattr(session,key) for key in STATE_FIELDS}))
    if sum(map(len,entries.values())) > MAX_EXPANDED:
        raise ProjectError('项目超过512MB展开大小限制，请减少视频抽帧或模拟输出')
    manifest = {'format':'water-quality-project', 'schema_version':1, 'name':scrub(name),
        'created_at':datetime.now(timezone.utc).isoformat(),
        'runtime':{'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__},
        'contents':{k:{'bytes':len(v),'sha256':sha256(v).hexdigest()} for k,v in entries.items()}}
    output = BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
        archive.writestr('manifest.json',_json(manifest))
        for key,value in entries.items():
            archive.writestr(key,value)
    raw=output.getvalue()
    if len(raw)>MAX_PACKAGE:
        raise ProjectError('项目包超过256MB限制')
    return raw


@dataclass
class ProjectSnapshot:
    name: str
    state: dict
    artifacts: dict


def load_project(raw):
    """校验所有内容后再返回；不执行代码、不解压到用户指定路径。"""
    if len(raw)>MAX_PACKAGE:
        raise ProjectError('项目包超过256MB限制')
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            infos=archive.infolist()
            names=[i.filename for i in infos]
            if (len(infos)>4096 or len(names)!=len(set(names)) or
                    sum(i.file_size for i in infos)>MAX_EXPANDED):
                raise ProjectError('项目包文件数、重复名称或展开大小不合法')
            if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size>1024**2:
                raise ProjectError('缺少有效项目清单')
            manifest=_read_json(archive.read('manifest.json'))
            if manifest.get('format')!='water-quality-project' or manifest.get('schema_version')!=1:
                raise ProjectError('不支持的项目格式或版本')
            contents=manifest['contents']
            if set(contents)!=(set(names)-{'manifest.json'}) or 'state.json' not in contents:
                raise ProjectError('项目清单与文件不一致')
            entries={}
            for name,descriptor in contents.items():
                if not (name=='state.json' or re.fullmatch(r'arrays/\d{5}\.npy',name)
                        or name in {f'artifacts/{f}' for f in MODEL_FILES}):
                    raise ProjectError('项目包含未允许的路径或文件')
                value=archive.read(name)
                if len(value)!=descriptor['bytes'] or sha256(value).hexdigest()!=descriptor['sha256']:
                    raise ProjectError('项目内容校验失败')
                entries[name]=value

        def unpack(value,depth=0):
            if depth>50:
                raise ProjectError('项目数据嵌套过深')
            child=lambda v:unpack(v,depth+1)
            if value is None or isinstance(value,(str,bool,int,float)):
                return value
            if not isinstance(value,dict):
                raise ProjectError('项目数据节点无效')
            kind=value['kind']
            if kind=='array':
                file=value['file']
                if not re.fullmatch(r'arrays/\d{5}\.npy',file):
                    raise ProjectError('数组引用路径无效')
                stream=BytesIO(entries[file])
                version=np.lib.format.read_magic(stream)
                if version not in ((1,0),(2,0)):
                    raise ProjectError('不支持的数组格式版本')
                reader=np.lib.format.read_array_header_1_0 if version==(1,0) else np.lib.format.read_array_header_2_0
                shape,fortran,dtype=reader(stream)
                count=1
                for dimension in shape:
                    if dimension<0 or dimension>20_000_000:
                        raise ProjectError('数组形状超限')
                    count*=dimension
                payload=stream.read()
                if dtype.kind not in 'biufMm' or dtype.hasobject or count*dtype.itemsize!=len(payload):
                    raise ProjectError('数组类型或尺寸不合法')
                return np.frombuffer(payload,dtype=dtype).reshape(shape,order='F' if fortran else 'C').copy()
            if kind=='mapping':
                return {child(k):child(v) for k,v in value['items']}
            if kind in ('list','tuple'):
                result=[child(v) for v in value['items']]
                return tuple(result) if kind=='tuple' else result
            if kind=='float':
                if value['value'] not in ('nan','inf','-inf'):
                    raise ProjectError('特殊数值无效')
                return float(value['value'])
            if kind=='timestamp':
                return pd.Timestamp(value['value'])
            if kind=='timedelta':
                return pd.Timedelta(value['value'],unit='ns')
            if kind=='dataframe':
                index=pd.Index(child(value['index']),name=child(value['index_name']))
                frame=pd.DataFrame(index=index)
                for column in value['columns']:
                    frame[child(column['name'])]=pd.Series(child(column['values']),index=index,dtype=column['dtype'])
                frame.attrs=child(value['attrs'])
                return frame
            if kind in CLASSES:
                cls=CLASSES[kind]
                allowed={f.name for f in fields(cls) if f.metadata.get('persist',True)}
                if not set(value['fields'])<=allowed:
                    raise ProjectError('模型包含未知字段')
                return cls(**{k:child(v) for k,v in value['fields'].items()})
            raise ProjectError('项目包含未知的数据类型')

        state=unpack(_read_json(entries['state.json']))
        if not isinstance(state,dict) or set(state)!=set(STATE_FIELDS):
            raise ProjectError('项目状态字段不完整')
        _validate_state(state)
        name=manifest['name']
        if not isinstance(name,str) or not 1<=len(name.strip())<=100:
            raise ProjectError('项目名称无效')
        return ProjectSnapshot(name,state,{PurePosixPath(k).name:v for k,v in entries.items() if k.startswith('artifacts/')})
    except ProjectError:
        raise
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,OverflowError,zipfile.BadZipFile,RuntimeError,EOFError,OSError,zlib.error,RecursionError) as exc:
        raise ProjectError('项目包损坏或数据结构不兼容') from exc


def _validate_state(state):
    for key in ('data_filename','video_filename','analysis_status'):
        if not isinstance(state[key],str):
            raise ProjectError('项目文本字段无效')
    if type(state['analysis_done']) is not bool or type(state['analysis_revision']) is not int or state['analysis_revision']<0:
        raise ProjectError('项目运行状态无效')
    for key in ('black_spots_json','comprehensive_summary'):
        if state[key] is not None and not isinstance(state[key],str):
            raise ProjectError('项目摘要字段无效')
    for key in ('cross_validation','national_station_result'):
        if state[key] is not None and not isinstance(state[key],dict):
            raise ProjectError('项目证据字段无效')
    for key in ('river_ui','captions','tool_evidence','analysis_steps'):
        if not isinstance(state[key],dict):
            raise ProjectError('项目界面状态无效')
    for key in ('river_ui','captions','tool_evidence','analysis_steps','cross_validation',
                'national_station_result','messages','full_messages','video_frames'):
        _json(state[key])  # 供HTTP返回的字段不能包含非有限数或隐藏的数组/对象节点。
    if state['target_water_class'] not in ('I','II','III','IV','V'):
        raise ProjectError('项目水质等级无效')
    for key in ('df','black_spots'):
        if state[key] is not None and not isinstance(state[key],pd.DataFrame):
            raise ProjectError('监测表格式无效')
    if state['df'] is not None and not state['df'].empty:
        if 'datetime' not in state['df'] or not pd.api.types.is_datetime64_any_dtype(state['df']['datetime']):
            raise ProjectError('监测表缺少有效时间列')
    for key in ('video_frames','messages','full_messages'):
        if not isinstance(state[key],list) or any(not isinstance(v,dict) for v in state[key]):
            raise ProjectError('帧或对话记录格式无效')
    if not isinstance(state['river_ui'],dict) or not isinstance(state['analysis_revision'],int):
        raise ProjectError('项目状态版本无效')
    model=state['river_state']
    if model is None:
        return
    if not isinstance(model,RiverModelState) or not isinstance(model.config,RiverConfig):
        raise ProjectError('河道状态类型无效')
    cfg=model.config
    _json(asdict(cfg))
    _json(model.scenarios)
    for field in fields(cfg):
        number=getattr(cfg,field.name)
        if not isinstance(number,(str,float,int,tuple,dict,type(None))):
            raise ProjectError('河道参数类型无效')
        if isinstance(number,(float,int)) and not np.isfinite(number):
            raise ProjectError('河道参数包含非有限数')
    for key,lo,hi in (('length_m',10,100000),('width_avg_m',.5,5000),('depth_avg_m',.1,100),
                      ('mannings_n',1e-8,.2)):
        number=getattr(cfg,key)
        if not isinstance(number,(float,int)) or not lo<=number<=hi:
            raise ProjectError('河段参数超出当前支持范围')
    if model.model_initialized:
        if not 3<=len(model.cross_sections)<=100:
            raise ProjectError('断面数量无效')
        for section in model.cross_sections:
            if (not isinstance(section,CrossSection) or section.stations.ndim!=2 or
                section.stations.shape[1]!=2 or len(section.stations)<2 or
                not np.isfinite(section.stations).all() or np.any(np.diff(section.stations[:,0])<=0)):
                raise ProjectError('断面几何无效')
    for result in (model.hydro_result,model.wq_result):
        if result is None:
            continue
        if not isinstance(result,(HydroResult,WQResult)):
            raise ProjectError('模型结果类型无效')
        if not isinstance(result.params,dict):
            raise ProjectError('模型元数据无效')
        _json(result.params)
        for axis in (result.t,result.chainage):
            if not isinstance(axis,np.ndarray) or axis.ndim!=1 or not len(axis) or not np.isfinite(axis).all() or np.any(np.diff(axis)<=0) or axis[0]<0:
                raise ProjectError('模型时间或桩号轴无效')
        for f in fields(result):
            array=getattr(result,f.name)
            if isinstance(result,HydroResult) and f.name not in ('t','chainage','params') and array is None:
                raise ProjectError('水动力结果缺少必需数组')
            if f.name not in ('t','chainage','params') and array is not None:
                if not isinstance(array,np.ndarray) or array.shape!=(len(result.t),len(result.chainage)) or not np.isfinite(array).all():
                    raise ProjectError('模型结果数组尺寸或数值无效')
