"""WeChat subscribe-message sender; content comes only from approved template config."""
import httpx
from config import get_settings

class MessageError(Exception):
    def __init__(self,code,retryable=False,unknown=False):
        self.code=str(code);self.retryable=retryable;self.unknown=unknown
        super().__init__(self.code)

def send_message(user_id,template):
    s=get_settings()
    if not s.wechat_appid or not s.wechat_secret:raise MessageError('MESSAGE_NOT_CONFIGURED')
    try:
        response=httpx.get('https://api.weixin.qq.com/cgi-bin/token',params={'grant_type':'client_credential','appid':s.wechat_appid,'secret':s.wechat_secret},timeout=10)
        response.raise_for_status();token=response.json()
    except (httpx.HTTPError,ValueError):raise MessageError('TOKEN_UNAVAILABLE',retryable=True)
    if not token.get('access_token'):raise MessageError('TOKEN_'+str(token.get('errcode','ERROR')),retryable=token.get('errcode') in (-1,45009))
    try:
        response=httpx.post('https://api.weixin.qq.com/cgi-bin/message/subscribe/send',params={'access_token':token['access_token']},json={
            'touser':user_id,'template_id':template['id'],'page':template['page'],'data':template['data'],
            'miniprogram_state':template.get('miniprogram_state','formal'),'lang':'zh_CN'},timeout=10)
        response.raise_for_status();result=response.json()
    except (httpx.HTTPError,ValueError):
        # Provider has no request-id replay guarantee; a timeout must never cause duplicate lockscreen messages.
        raise MessageError('SEND_RESULT_UNKNOWN',unknown=True)
    code=result.get('errcode')
    if code!=0:raise MessageError('WECHAT_'+str(code),retryable=code in (-1,40001,42001,45009))
