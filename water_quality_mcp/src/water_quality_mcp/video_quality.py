"""视频画面质量及人工水面区域；视觉线索不作为浊度、黑臭或污染真值。"""
import base64
from hashlib import sha256
import json
import cv2
import numpy as np

VERSION = 'video-quality-v1'
NOTICE = ('阈值为未现场率定的画面复核设置；低纹理可能是平静水面，亮斑可能是反光。'
          '水面区域和同步为人工声明，未经独立核验；颜色、清晰度不能替代浊度或黑臭实测。')
DEFAULTS = {'roi':[0.,0.,1.,1.], 'roi_confirmed':False, 'roi_note':'', 'reviewer':'',
    'sync_confirmed':False, 'sync_note':'', 'time_offset_sec':0., 'time_window_sec':30.}
LABELS = {'invalid_frame':'帧时间或图像无法读取', 'roi_unconfirmed':'水面区域未核实',
    'roi_too_small':'区域像素不足', 'low_texture_or_blur':'低纹理或模糊，需人工区分',
    'low_contrast':'对比度低', 'too_dark':'大面积过暗', 'glare':'亮斑或过曝比例较高',
    'manual_exclusion':'人工排除本帧'}


def validate_roi(value):
    if (not isinstance(value, list) or len(value)!=4
            or any(type(v) not in (int,float) or not np.isfinite(v) or not 0<=v<=1 for v in value)
            or value[2]-value[0]<.02 or value[3]-value[1]<.02):
        raise ValueError('水面区域须为0～1的[x左,y上,x右,y下]，宽高至少为画面的2%')
    return [float(v) for v in value]


def validate_settings(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULTS):
        raise ValueError('视频复核设置字段无效')
    result={**DEFAULTS,**value};result['roi']=validate_roi(result['roi'])
    for key in ('roi_confirmed','sync_confirmed'):
        if type(result[key]) is not bool:raise ValueError('确认字段须为布尔值')
    for key,maximum in (('roi_note',1000),('sync_note',1000),('reviewer',100)):
        if not isinstance(result[key],str) or len(result[key])>maximum:raise ValueError('复核说明长度或格式无效')
    for key,low,high in (('time_offset_sec',-86400,86400),('time_window_sec',.001,3600)):
        v=result[key]
        if type(v) not in (int,float) or not np.isfinite(v) or not low<=v<=high:
            raise ValueError('同步时间偏移或窗口超出支持范围')
    for flag,note in (('roi_confirmed','roi_note'),('sync_confirmed','sync_note')):
        if result[flag] and (not result[note].strip() or not result['reviewer'].strip()):
            raise ValueError('确认区域或同步时须记录复核人及依据')
    return result


def settings_for(frames):
    return validate_settings(frames[0].get('video_settings',{}) if frames else {})


def validate_review(value):
    if not isinstance(value,dict) or set(value)-{'decision','reviewer','notes','roi','reviewed_at'}:
        raise ValueError('帧复核字段无效')
    result={'decision':'pending','reviewer':'','notes':'','roi':None,**value}
    if result['decision'] not in ('pending','include','exclude'):raise ValueError('帧复核状态无效')
    if any(not isinstance(result[k],str) or len(result[k])>n for k,n in (('reviewer',100),('notes',1000))):
        raise ValueError('帧复核说明格式无效')
    if result['decision']!='pending' or result['roi'] is not None:
        if not result['reviewer'].strip() or not result['notes'].strip():raise ValueError('帧检查须记录检查人和说明')
    if result['roi'] is not None:result['roi']=validate_roi(result['roi'])
    if 'reviewed_at' in result and not isinstance(result['reviewed_at'],str):raise ValueError('复核时间格式无效')
    return result


def revision(frames):
    digest=sha256(VERSION.encode())
    for frame in frames:
        digest.update(json.dumps(frame,ensure_ascii=False,sort_keys=True,allow_nan=False).encode())
    return digest.hexdigest()


def decode_frame(frame):
    try:
        stamp=frame['time_sec']
        if type(stamp) not in (int,float) or not np.isfinite(stamp) or stamp<0:raise ValueError('时间无效')
        raw=base64.b64decode(frame['image_base64'],validate=True)
        image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
        if image is None or not image.size:raise ValueError('图像无法解码')
        return image
    except (KeyError,ValueError,TypeError,cv2.error) as exc:
        raise ValueError('帧时间或图像无法读取') from exc


def inspect_frame(frame,index,settings):
    review=validate_review(frame.get('review',{}))
    roi=review['roi'] or settings['roi']
    confirmed=review['roi'] is not None or settings['roi_confirmed']
    out={'id':index,'time_sec':None,'roi':roi,'roi_confirmed':confirmed,'review':review,
        'features':None,'dark_color_cue':None,'quality_flags':[],'width':None,'height':None}
    flags=out['quality_flags']
    try:
        image=decode_frame(frame);out['time_sec']=float(frame['time_sec'])
        height,width=image.shape[:2];out.update(width=width,height=height)
        left,top,right,bottom=roi
        crop=image[int(top*height):int(np.ceil(bottom*height)),int(left*width):int(np.ceil(right*width))]
        if crop.shape[0]<16 or crop.shape[1]<16:flags.append('roi_too_small')
        else:
            # 统一最长边并限制计算像素；低纹理只触发复核，不断言相机失焦。
            scale=320/max(crop.shape[:2])
            crop=cv2.resize(crop,(max(16,round(crop.shape[1]*scale)),max(16,round(crop.shape[0]*scale))),interpolation=cv2.INTER_AREA)
            gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY);hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
            s,v=hsv[:,:,1],hsv[:,:,2]
            lap=float(cv2.Laplacian(gray,cv2.CV_64F).var());contrast=float(gray.std())
            dark=float((v<25).mean());glare=float(((v>=245)&(s<=45)).mean())
            out['features']={'laplacian_var':lap,'mean_saturation':float(s.mean()),
                'mean_brightness':float(v.mean()),'contrast_std':contrast,'dark_fraction':dark,'glare_fraction':glare}
            if lap<30:flags.append('low_texture_or_blur')
            if contrast<8:flags.append('low_contrast')
            if dark>.5:flags.append('too_dark')
            if glare>.15:flags.append('glare')
    except ValueError:flags.append('invalid_frame')
    if not confirmed:flags.append('roi_unconfirmed')
    if review['decision']=='exclude':flags.append('manual_exclusion')
    out['eligible']=not flags
    out['quality_status']='invalid' if 'invalid_frame' in flags else 'needs_review' if flags else 'usable'
    out['quality_messages']=[LABELS[key] for key in flags]
    if out['eligible']:
        f=out['features'];out['dark_color_cue']=f['mean_saturation']<40 and f['mean_brightness']<70
    return out


def inspect_video(frames):
    settings=settings_for(frames)
    rows=[inspect_frame(frame,i,settings) for i,frame in enumerate(frames)]
    return {'version':VERSION,'settings':settings,'summary':{'frames':len(rows),
        'eligible':sum(row['eligible'] for row in rows),'invalid':sum(row['quality_status']=='invalid' for row in rows),
        'needs_review':sum(row['quality_status']=='needs_review' for row in rows),
        'manual_excluded':sum(row['review']['decision']=='exclude' for row in rows)},'rows':rows,'notice':NOTICE}
