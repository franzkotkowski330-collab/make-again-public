"""Bounded upload validation and short-lived private local media URLs."""
import hashlib,hmac,time,io,wave,subprocess,tempfile,os,json
from pathlib import Path
from fastapi import APIRouter,Depends
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from config import get_settings
from api.security import SecureRoute, fail, iso
from memory.db import get_session
from memory import models as m
from sqlalchemy import select

router=APIRouter(prefix='/api',route_class=SecureRoute)

def read_upload(file,kind):
    s=get_settings();limit=(s.speech_max_upload_mb if kind=='audio' else s.item_max_upload_mb)*1024*1024
    data=file.file.read(limit+1)
    if len(data)>limit:fail(413,'UPLOAD_TOO_LARGE','文件超过大小限制')
    if not data:fail(422,'EMPTY_UPLOAD','文件为空')
    if kind=='image':
        try:
            with Image.open(io.BytesIO(data)) as im:
                if im.width*im.height>24000000:fail(413,'IMAGE_TOO_LARGE','图片尺寸过大')
                if im.format not in ('JPEG','PNG','WEBP'):fail(415,'UNSUPPORTED_IMAGE','仅支持 JPEG、PNG、WebP')
                mime=Image.MIME[im.format];ext='.'+('jpg' if im.format=='JPEG' else im.format.lower());im.verify()
            with Image.open(io.BytesIO(data)) as im: im.load()
        except (UnidentifiedImageError,OSError,Image.DecompressionBombError):fail(415,'INVALID_IMAGE','图片内容无效')
        return data,ext,mime
    if data[:4]==b'RIFF' and data[8:12]==b'WAVE':
        try:
            with wave.open(io.BytesIO(data),'rb') as w:
                if w.getnchannels() not in (1,2) or w.getsampwidth() not in (1,2,3,4) or not 8000<=w.getframerate()<=48000 or w.getnframes()/w.getframerate()>180:
                    fail(415,'UNSUPPORTED_AUDIO','不支持此音频参数')
                expected=w.getnframes()*w.getnchannels()*w.getsampwidth()
                if expected==0 or len(w.readframes(w.getnframes()))!=expected:fail(415,'INVALID_AUDIO','音频不完整')
        except (wave.Error,EOFError):fail(415,'INVALID_AUDIO','音频不完整')
        return data,'.wav','audio/wav'
    # MP3 is decoded, never accepted on a filename or ID3 magic alone.
    if data.startswith(b'ID3') or (len(data)>1 and data[0]==255 and data[1]&224==224):
        import shutil
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg:fail(503,'AUDIO_DECODER_UNAVAILABLE','音频解码服务未配置')
        with tempfile.NamedTemporaryFile(suffix='.mp3') as f:
            f.write(data);f.flush()
            try:
                run=subprocess.run([ffmpeg,'-v','error','-xerror','-i',f.name,'-t','181','-f','s16le','-ac','1','-ar','16000','pipe:1'],capture_output=True,timeout=15)
            except subprocess.TimeoutExpired:fail(415,'INVALID_AUDIO','音频解码超时')
            if run.returncode or not run.stdout or len(run.stdout)>180*32000:fail(415,'INVALID_AUDIO','MP3 无效或时长超过限制')
        return data,'.mp3','audio/mpeg'
    fail(415,'UNSUPPORTED_AUDIO','仅支持有效的 WAV 或 MP3 音频')

def local_url(key,expires):
    s=get_settings();exp=int(time.time())+expires
    sig=hmac.new(s.media_signing_key.encode(),f'{key}\n{exp}'.encode(),hashlib.sha256).hexdigest()
    from urllib.parse import quote
    return f'{s.public_base_url.rstrip("/")}/api/media/local/{quote(key)}?expires={exp}&signature={sig}'

def verify_local(key,expires,signature):
    s=get_settings()
    if expires<int(time.time()) or expires>int(time.time())+3600:fail(403,'MEDIA_EXPIRED','图片地址已过期，请刷新列表')
    expected=hmac.new(s.media_signing_key.encode(),f'{key}\n{expires}'.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,expected):fail(403,'MEDIA_FORBIDDEN','无效的媒体授权')
    root=Path(s.storage_local_dir).resolve();path=(root/key).resolve()
    if root not in path.parents or not path.is_file():fail(404,'MEDIA_NOT_FOUND','文件不存在')
    return path

# Signed URLs are bearer capabilities, verified before returning any bytes.
@router.get('/media/local/{key:path}')
def media_local(key:str,expires:int,signature:str):
    return FileResponse(verify_local(key,expires,signature),headers={'Cache-Control':'private, max-age=0','X-Content-Type-Options':'nosniff'})

def photo_view(row):
    from gateway.storage import ObjectStore
    return {'id':row.id,'photo_id':row.id,'companion_id':row.companion_id,'client_request_id':row.client_request_id,
            'title':row.title,'description':row.description,'photo_url':ObjectStore().presigned_url(row.photo_key),
            'created_at':iso(row.ts),'status':row.processing_status}

def item_view(row):
    from gateway.storage import ObjectStore
    obj=ObjectStore()
    return {'id':row.id,'item_id':row.id,'companion_id':row.companion_id,'client_request_id':row.client_request_id,
            'item_name':row.item_name,'description':row.description,'intent':row.intent,
            'cutout_url':obj.presigned_url(row.cutout_key or row.original_key),
            'original_url':obj.presigned_url(row.original_key) if row.original_key else None,
            'created_at':iso(row.ts),'status':row.processing_status}
