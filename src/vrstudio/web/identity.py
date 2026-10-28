"""免登录昵称身份（决策 17 的当前实现）。

- `vrstudio_uid` 是随机生成并长期保存的稳定 id，用于任务与 case 归属。
- `vrstudio_name` 是显示名，只用于页面显示与任务中心；不做认证。
- 两者都通过接口隔离，未来换账号体系时新增 `AccountProvider`，页面不改。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from urllib.parse import unquote

from starlette.requests import Request

UID_COOKIE = "vrstudio_uid"
NAME_COOKIE = "vrstudio_name"
DEFAULT_NAME = "访客"
MAX_NAME_LENGTH = 40
_UID_MAX_AGE = 365 * 24 * 3600


@dataclass(frozen=True)
class User:
    id: str
    display_name: str
    authenticated: bool = False


class NicknameProvider:
    """从 cookie 解析使用者；没有 uid 时生成一个新的稳定 id。"""

    def resolve(self, request: Request) -> tuple[User, str | None]:
        uid = request.cookies.get(UID_COOKIE)
        new_uid: str | None = None
        if not uid:
            uid = uuid.uuid4().hex
            new_uid = uid
        # 昵称按 UTF-8 百分号编码写进 cookie（HTTP 头只允许 ASCII），这里解回来。
        name = unquote(request.cookies.get(NAME_COOKIE) or "").strip()
        if not name:
            name = DEFAULT_NAME
        return User(uid, name[:MAX_NAME_LENGTH]), new_uid


class IdentityMiddleware:
    """把当前使用者放进 ``request.state.user``，并在首次访问时下发 uid。"""

    def __init__(self, app, provider: NicknameProvider | None = None):
        self.app = app
        self.provider = provider or NicknameProvider()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        user, new_uid = self.provider.resolve(Request(scope))
        scope.setdefault("state", {})["user"] = user

        async def send_wrapper(message):
            if new_uid and message["type"] == "http.response.start":
                cookie = (f"{UID_COOKIE}={new_uid}; Path=/; HttpOnly; "
                          f"SameSite=Lax; Max-Age={_UID_MAX_AGE}")
                message.setdefault("headers", []).append(
                    (b"set-cookie", cookie.encode("ascii")))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def current_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if isinstance(user, User):
        return user
    return User("anonymous", DEFAULT_NAME)
