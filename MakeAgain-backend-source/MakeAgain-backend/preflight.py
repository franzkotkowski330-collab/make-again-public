"""Offline runtime checks. Never prints environment values or accesses user rows."""
import json,shutil,os
from pathlib import Path
from config import get_settings,validate_runtime

def check():
    s=get_settings();errors=[]
    try:validate_runtime()
    except RuntimeError as e:errors.append(str(e))
    if not s.database_url.startswith('sqlite:///'):errors.append('B07 migration supports SQLite only')
    if not shutil.which('ffmpeg'):errors.append('FFmpeg decoder missing from PATH (required for MP3)')
    if s.environment=='production' and not s.database_url.startswith('sqlite:////'):errors.append('Production DATABASE_URL must use an explicit absolute path')
    payment_fields=[s.wechat_pay_mchid,s.wechat_pay_serial_no,s.wechat_pay_private_key_path,s.wechat_pay_platform_key_path,s.wechat_pay_platform_serial,s.wechat_pay_api_v3_key,s.wechat_pay_notify_url,s.payment_allowed_platforms]
    if any(payment_fields):
        from gateway.payments import configured
        if not configured():errors.append('Payment configuration is partial; complete it or leave payments unavailable')
        for filename in (s.wechat_pay_private_key_path,s.wechat_pay_platform_key_path):
            if not filename or not Path(filename).is_file():errors.append('Payment key file unavailable')
    from api.subscriptions import templates
    if s.wechat_subscribe_templates not in ('','[]') and not templates():errors.append('Message template configuration invalid')
    return {'ok':not errors,'errors':errors,'checks':['runtime mode','SQLite path','FFmpeg availability','payment key files','message template structure']}

if __name__=='__main__':
    report=check();print(json.dumps(report,ensure_ascii=False));raise SystemExit(0 if report['ok'] else 1)
