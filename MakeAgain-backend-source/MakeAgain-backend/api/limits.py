"""Bounded incoming body reads, including chunked multipart requests."""
from starlette.responses import JSONResponse
class BodyTooLarge(Exception):pass
class BodyLimitMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]));multipart=b'multipart/' in headers.get(b'content-type',b'')
        maximum=22*1024*1024 if multipart else 256*1024
        size=0
        async def bounded():
            nonlocal size
            message=await receive()
            if message['type']=='http.request':
                size+=len(message.get('body',b''))
                if size>maximum:raise BodyTooLarge()
            return message
        await self.app(scope,bounded,send)
