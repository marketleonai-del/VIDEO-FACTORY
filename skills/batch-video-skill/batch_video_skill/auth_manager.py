# -*- coding: utf-8 -*-
"""
认证管理器 — 统一认证与凭证管理基础设施

位置: batch_video_skill/infrastructure/auth_manager.py
功能: 4种认证策略 + CredentialMonitor + 安全存储

设计目标:
1. 策略模式统一接口 — 所有认证方式统一实现 AuthStrategy 接口
2. 凭证生命周期管理 — 创建 → 验证 → 刷新 → 过期检测 → 销毁
3. 安全存储 — 支持密钥环 / 加密文件 / 环境变量三层后端
4. 自动过期检测 — 后台线程定期检测凭证健康
5. 多服务管理 — 同时管理多个服务的认证

支持服务:
- Gemini (Cookie 认证)
- Google Workspace (Service Account / OAuth2)
- 即梦/jimeng (API Key)
- 可灵/kling (API Key)
- Vidu (API Key)
- Veo3 (API Key)
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type

try:
    from pydantic import BaseModel, Field, SecretStr, field_validator
except ImportError:
    raise ImportError("pydantic >= 2.0 is required")

try:
    import requests
except ImportError:
    requests = None  # type: ignore

logger = logging.getLogger(__name__)


# ============================================================================
# 枚举类型
# ============================================================================


class AuthType(str, Enum):
    """支持的认证类型枚举"""

    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    SERVICE_ACCOUNT = "service_account"
    COOKIE = "cookie"


class CredentialStatus(str, Enum):
    """凭证健康状态枚举"""

    HEALTHY = "healthy"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"
    INVALID = "invalid"
    UNKNOWN = "unknown"
    NOT_CONFIGURED = "not_configured"


# ============================================================================
# Pydantic 数据模型
# ============================================================================


class Credentials(BaseModel):
    """统一凭证数据模型 — 所有认证方式共享"""

    model_config = {"extra": "allow"}

    # 基础元数据
    auth_type: AuthType = Field(..., description="认证类型")
    endpoint: Optional[str] = Field(default=None, description="API 基础 URL")
    scopes: List[str] = Field(default_factory=list, description="OAuth Scope 列表")

    # API Key 认证
    api_key: Optional[SecretStr] = Field(default=None, description="API Key")
    api_key_index: int = Field(default=0, ge=0, description="多 Key 轮询索引")

    # OAuth2 认证
    client_id: Optional[str] = Field(default=None)
    client_secret: Optional[SecretStr] = Field(default=None)
    refresh_token: Optional[SecretStr] = Field(default=None)
    access_token: Optional[str] = Field(default=None)
    token_expiry: Optional[float] = Field(
        default=None, description="过期时间 Unix timestamp"
    )

    # 服务账号认证
    service_account_file: Optional[str] = Field(default=None)
    service_account_info: Optional[Dict[str, Any]] = Field(default=None)

    # Cookie 认证
    cookie_name: Optional[str] = Field(default=None)
    cookie_value: Optional[SecretStr] = Field(default=None)

    # 元数据
    created_at: float = Field(default_factory=time.time)
    last_validated_at: Optional[float] = Field(default=None)
    is_valid: bool = Field(default=True)
    validation_error: Optional[str] = Field(default=None)

    @field_validator("service_account_file")
    @classmethod
    def expand_path(cls: type["Credentials"], v: Optional[str]) -> Optional[str]:
        if v:
            return os.path.expanduser(v)
        return v

    @property
    def is_expired(self: "Credentials") -> bool:
        if self.token_expiry is None:
            return False
        return time.time() > self.token_expiry

    @property
    def days_until_expiry(self: "Credentials") -> Optional[float]:
        if self.token_expiry is None:
            return None
        return (self.token_expiry - time.time()) / 86400

    @property
    def needs_refresh(self: "Credentials") -> bool:
        if self.token_expiry is None:
            return False
        return time.time() > (self.token_expiry - 300)

    def to_safe_dict(self: "Credentials") -> Dict[str, Any]:
        """脱敏安全字典，用于日志和监控"""
        return {
            "auth_type": self.auth_type.value,
            "endpoint": self.endpoint,
            "scopes": self.scopes,
            "key_prefix": self._mask_secret(self.api_key),
            "client_id_prefix": self._mask_string(self.client_id),
            "cookie_name": self.cookie_name,
            "cookie_prefix": self._mask_secret(self.cookie_value),
            "service_account_file": self.service_account_file,
            "is_valid": self.is_valid,
            "is_expired": self.is_expired,
            "days_until_expiry": (
                round(self.days_until_expiry, 1)
                if self.days_until_expiry is not None
                else None
            ),
            "last_validated": (
                datetime.fromtimestamp(self.last_validated_at).isoformat()
                if self.last_validated_at
                else None
            ),
            "validation_error": self.validation_error,
        }

    @staticmethod
    def _mask_secret(secret: Optional[SecretStr]) -> Optional[str]:
        if secret is None:
            return None
        val = secret.get_secret_value()
        if len(val) <= 8:
            return "***"
        return val[:6] + "***" + val[-3:]

    @staticmethod
    def _mask_string(s: Optional[str]) -> Optional[str]:
        if s is None:
            return None
        if len(s) <= 8:
            return "***"
        return s[:6] + "***"

    def get_effective_api_key(self: "Credentials") -> Optional[str]:
        if self.api_key:
            return self.api_key.get_secret_value()
        return None

    def get_effective_cookie(self: "Credentials") -> Optional[str]:
        if self.cookie_value and self.cookie_name:
            return f"{self.cookie_name}={self.cookie_value.get_secret_value()}"
        return None


class CredentialHealth(BaseModel):
    """凭证健康状态数据模型"""

    model_config = {"extra": "allow"}

    service: str = Field(...)
    status: CredentialStatus = Field(default=CredentialStatus.UNKNOWN)
    last_checked: Optional[float] = Field(default=None)
    expires_at: Optional[float] = Field(default=None)
    message: str = Field(default="")
    details: Dict[str, Any] = Field(default_factory=dict)

    @property
    def days_until_expiry(self: "CredentialHealth") -> Optional[float]:
        if self.expires_at:
            return (self.expires_at - time.time()) / 86400
        return None

    def to_report_dict(self: "CredentialHealth") -> Dict[str, Any]:
        return {
            "service": self.service,
            "status": self.status.value,
            "last_checked": (
                datetime.fromtimestamp(self.last_checked).isoformat()
                if self.last_checked
                else None
            ),
            "expires_at": (
                datetime.fromtimestamp(self.expires_at).isoformat()
                if self.expires_at
                else None
            ),
            "days_until_expiry": (
                round(self.days_until_expiry, 1)
                if self.days_until_expiry is not None
                else None
            ),
            "message": self.message,
            "details": self.details,
        }


class HealthReport(BaseModel):
    """完整健康报告"""

    model_config = {"extra": "allow"}

    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    total_services: int = Field(default=0)
    healthy: int = Field(default=0)
    expiring_soon: int = Field(default=0)
    expired: int = Field(default=0)
    invalid_count: int = Field(default=0)
    not_configured: int = Field(default=0)
    services: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    @property
    def has_issues(self: "HealthReport") -> bool:
        return (self.expired + self.invalid_count + self.expiring_soon) > 0


# ============================================================================
# 认证异常
# ============================================================================


class AuthError(Exception):
    """认证错误基类"""

    def __init__(self: "AuthError", message: str = "认证错误", cause: Optional[Exception] = None) -> None:
        self.cause = cause
        super().__init__(message)


class AuthNotFoundError(AuthError):
    """认证配置未找到"""

    def __init__(self: "AuthNotFoundError", service: str = "") -> None:
        super().__init__(f"服务 '{service}' 未配置认证")


class AuthNotConfiguredError(AuthError):
    """认证未配置"""

    def __init__(self: "AuthNotConfiguredError", message: str = "认证未配置") -> None:
        super().__init__(message)


class AuthValidationError(AuthError):
    """认证验证失败"""

    def __init__(self: "AuthValidationError", message: str = "认证验证失败", cause: Optional[Exception] = None) -> None:
        super().__init__(message, cause=cause)


class AuthRefreshError(AuthError):
    """认证刷新失败"""

    def __init__(self: "AuthRefreshError", message: str = "认证刷新失败", cause: Optional[Exception] = None) -> None:
        super().__init__(message, cause=cause)


# ============================================================================
# 认证策略抽象基类
# ============================================================================


class AuthStrategy(ABC):
    """
    认证策略抽象基类

    所有认证方式必须实现此接口。

    生命周期:
        1. 创建: __init__(credentials)
        2. 验证: validate() -> bool
        3. 使用: apply_auth(request) -> request
        4. 刷新: refresh() -> bool (如果 is_refreshable)
        5. 检查: is_valid / is_expired
    """

    _auth_type: AuthType  # 子类必须覆盖

    def __init__(self: "AuthStrategy", credentials: Credentials) -> None:
        self._credentials = credentials
        self._session = requests.Session() if requests else None
        self._validate_credentials()

    @property
    @abstractmethod
    def auth_type(self: "AuthStrategy") -> AuthType:
        """返回此策略处理的认证类型"""
        pass

    @property
    def credentials(self: "AuthStrategy") -> Credentials:
        """获取凭证数据（只读引用）"""
        return self._credentials

    @property
    def is_valid(self: "AuthStrategy") -> bool:
        """凭证是否有效（基于上次验证结果）"""
        return self._credentials.is_valid

    @property
    def is_expired(self: "AuthStrategy") -> bool:
        """凭证是否已过期"""
        return self._credentials.is_expired

    @property
    def is_refreshable(self: "AuthStrategy") -> bool:
        """此认证类型是否支持自动刷新"""
        return False

    @abstractmethod
    def get_auth_header(self: "AuthStrategy") -> Dict[str, str]:
        """获取认证头字典"""
        pass

    @abstractmethod
    def apply_auth(self: "AuthStrategy", request: Any) -> Any:
        """将认证信息应用到 HTTP 请求中"""
        pass

    @abstractmethod
    def validate(self: "AuthStrategy") -> bool:
        """
        验证凭证是否有效

        Returns:
            True=有效, False=无效
        """
        pass

    @abstractmethod
    def refresh(self: "AuthStrategy") -> bool:
        """
        刷新凭证（如果支持）

        Returns:
            True=刷新成功, False=刷新失败或不支持
        """
        pass

    @abstractmethod
    def to_summary(self: "AuthStrategy") -> Dict[str, Any]:
        """获取凭证摘要（脱敏）"""
        pass

    def _validate_credentials(self: "AuthStrategy") -> None:
        """验证凭证数据的完整性和合法性"""
        if self._credentials.auth_type != self._auth_type:
            raise AuthError(
                f"凭证类型不匹配: 期望 {self._auth_type.value}, "
                f"实际 {self._credentials.auth_type.value}"
            )

    def _update_validation(
        self, is_valid: bool, error_msg: Optional[str] = None
    ) -> None:
        """更新验证结果"""
        self._credentials.is_valid = is_valid
        self._credentials.last_validated_at = time.time()
        if not is_valid and error_msg:
            self._credentials.validation_error = error_msg
        elif is_valid:
            self._credentials.validation_error = None

    def __repr__(self: "AuthStrategy") -> str:
        return (
            f"<{self.__class__.__name__}"
            f"(type={self.auth_type.value}, valid={self.is_valid})>"
        )


# ============================================================================
# CookieAuth — Gemini Cookie 认证
# ============================================================================


class CookieAuth(AuthStrategy):
    """
    Cookie 认证策略 — 用于 Gemini Web 接口

    特性:
        - 使用浏览器 Cookie (__Secure-1PSID) 认证
        - 不支持自动刷新
        - 稳定性差，Cookie 可能随时失效
    """

    _auth_type = AuthType.COOKIE
    HEALTH_CHECK_URL = "https://gemini.google.com/app"
    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )

    @property
    def auth_type(self: "CookieAuth") -> AuthType:
        return AuthType.COOKIE

    @property
    def is_refreshable(self: "CookieAuth") -> bool:
        return False

    def get_auth_header(self: "CookieAuth") -> Dict[str, str]:
        cookie_str = self._credentials.get_effective_cookie()
        if not cookie_str:
            return {}
        return {"Cookie": cookie_str}

    def apply_auth(self: "CookieAuth", request: Any) -> Any:
        """将 Cookie 和浏览器头添加到请求"""
        if not self._session:
            return request
        cookie_str = self._credentials.get_effective_cookie()
        if cookie_str:
            request.headers["Cookie"] = cookie_str
            request.headers["User-Agent"] = self.USER_AGENT
            request.headers["Accept"] = (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            )
            request.headers["Accept-Language"] = "en-US,en;q=0.9"
            request.headers["Referer"] = "https://gemini.google.com/"
        return request

    def validate(self: "CookieAuth") -> bool:
        """验证 Cookie 是否有效"""
        if not self._session:
            self._update_validation(False, "requests 库未安装")
            return False

        cookie_str = self._credentials.get_effective_cookie()
        if not cookie_str:
            self._update_validation(False, "Cookie 未配置")
            return False

        try:
            resp = self._session.get(
                self.HEALTH_CHECK_URL,
                headers={
                    "Cookie": cookie_str,
                    "User-Agent": self.USER_AGENT,
                    "Accept": (
                        "text/html,application/xhtml+xml,"
                        "application/xml;q=0.9,*/*;q=0.8"
                    ),
                },
                allow_redirects=False,
                timeout=15,
            )

            if resp.status_code == 302:
                location = resp.headers.get("Location", "")
                if "accounts.google.com" in location:
                    self._update_validation(
                        False, "Cookie 已过期，需要重新从浏览器获取"
                    )
                    return False

            if resp.status_code == 200:
                self._update_validation(True)
                return True

            self._update_validation(
                False,
                f"Cookie 验证返回异常状态码: HTTP {resp.status_code}",
            )
            return False

        except Exception as e:
            logger.error("Cookie 验证网络请求失败: %s", e)
            self._update_validation(False, f"网络请求失败: {str(e)}")
            return False

    def refresh(self: "CookieAuth") -> bool:
        """Cookie 不支持自动刷新"""
        logger.warning(
            "Cookie 认证不支持自动刷新。"
            "请从浏览器重新获取 __Secure-1PSID。"
        )
        return False

    def to_summary(self: "CookieAuth") -> Dict[str, Any]:
        cookie = self._credentials.cookie_value
        cookie_prefix = None
        if cookie:
            val = cookie.get_secret_value()
            cookie_prefix = val[:12] + "..." if len(val) > 12 else "***"
        return {
            "type": "cookie",
            "cookie_name": self._credentials.cookie_name,
            "cookie_prefix": cookie_prefix,
            "endpoint": self._credentials.endpoint,
            "is_valid": self._credentials.is_valid,
            "is_refreshable": self.is_refreshable,
            "last_validated": self._credentials.last_validated_at,
        }


# ============================================================================
# APIKeyAuth — API Key 认证
# ============================================================================


class APIKeyAuth(AuthStrategy):
    """
    API Key 认证策略 — Bearer Token 方式

    适用服务: 即梦(jimeng)、可灵(kling)、Vidu(vidu)、Veo3(veo3)
    特性: 不支持自动刷新，支持多 Key 轮询
    """

    _auth_type = AuthType.API_KEY

    # 各服务的轻量验证端点
    VALIDATION_ENDPOINTS = {
        "jimeng": {
            "url": "https://ark.cn-beijing.volces.com/api/v3/endpoints",
            "method": "GET",
            "valid_statuses": [200, 401, 403],
        },
        "kling": {
            "url": "https://api.klingai.com/v1/account/rateLimit",
            "method": "GET",
            "valid_statuses": [200, 401],
        },
        "vidu": {
            "url": "https://api.vidu.com/v1/user",
            "method": "GET",
            "valid_statuses": [200, 401],
        },
        "veo3": {
            "url": "https://generativelanguage.googleapis.com/v1beta/models",
            "method": "GET",
            "params_key": "key",
            "valid_statuses": [200, 400],
        },
    }

    def __init__(
        self, credentials: Credentials, service_name: str = ""
    ) -> None:
        self._service_name = service_name
        super().__init__(credentials)

    @property
    def auth_type(self: "APIKeyAuth") -> AuthType:
        return AuthType.API_KEY

    @property
    def is_refreshable(self: "APIKeyAuth") -> bool:
        return False

    def get_auth_header(self: "APIKeyAuth") -> Dict[str, str]:
        api_key = self._credentials.get_effective_api_key()
        if not api_key:
            return {}
        return {"Authorization": f"Bearer {api_key}"}

    def apply_auth(self: "APIKeyAuth", request: Any) -> Any:
        """将 Bearer Token 添加到请求头"""
        api_key = self._credentials.get_effective_api_key()
        if not api_key:
            return request

        if (
            self._service_name == "veo3"
            and hasattr(request, "url")
            and request.url
            and "generativelanguage" in request.url
        ):
            import urllib.parse

            url_parts = list(urllib.parse.urlparse(request.url))
            query = dict(urllib.parse.parse_qsl(url_parts[4]))
            query["key"] = api_key
            url_parts[4] = urllib.parse.urlencode(query)
            request.url = urllib.parse.urlunparse(url_parts)
        else:
            request.headers["Authorization"] = f"Bearer {api_key}"
        return request

    def validate(self: "APIKeyAuth") -> bool:
        """通过调用轻量端点验证 API Key"""
        if not self._session:
            self._update_validation(False, "requests 库未安装")
            return False

        api_key = self._credentials.get_effective_api_key()
        if not api_key:
            self._update_validation(False, "API Key 未配置")
            return False

        endpoint_info = self.VALIDATION_ENDPOINTS.get(self._service_name)
        if not endpoint_info:
            self._update_validation(True)
            return True

        try:
            url = endpoint_info["url"]
            method = endpoint_info.get("method", "GET")
            valid_statuses = endpoint_info.get("valid_statuses", [200])

            if endpoint_info.get("params_key") == "key":
                params = {"key": api_key}
                headers = {}
            else:
                params = None
                headers = {"Authorization": f"Bearer {api_key}"}

            resp = self._session.request(
                method, url, headers=headers, params=params, timeout=10
            )

            if resp.status_code in valid_statuses:
                if resp.status_code == 401:
                    self._update_validation(
                        False, "API Key 无效或已被撤销"
                    )
                    return False
                self._update_validation(True)
                return True

            self._update_validation(
                False,
                f"API Key 验证返回异常状态码: HTTP {resp.status_code}",
            )
            return False

        except Exception as e:
            logger.error(
                "API Key 验证网络请求失败 [%s]: %s", self._service_name, e
            )
            self._update_validation(False, f"网络请求失败: {str(e)}")
            return False

    def refresh(self: "APIKeyAuth") -> bool:
        """API Key 不支持自动刷新"""
        logger.warning(
            "API Key 不支持自动刷新。请前往服务商控制台重新生成 Key。"
        )
        return False

    def to_summary(self: "APIKeyAuth") -> Dict[str, Any]:
        api_key = self._credentials.api_key
        key_prefix = None
        if api_key:
            val = api_key.get_secret_value()
            key_prefix = val[:8] + "..." if len(val) > 8 else "***"
        return {
            "type": "api_key",
            "service": self._service_name,
            "key_prefix": key_prefix,
            "endpoint": self._credentials.endpoint,
            "is_valid": self._credentials.is_valid,
            "is_refreshable": self.is_refreshable,
            "last_validated": self._credentials.last_validated_at,
        }


# ============================================================================
# ServiceAccountAuth — Google 服务账号认证
# ============================================================================


class ServiceAccountAuth(AuthStrategy):
    """
    Google 服务账号认证策略

    适用服务: Google Workspace, Veo3 Vertex AI
    特性: 支持自动刷新 Access Token
    依赖: pip install google-auth
    """

    _auth_type = AuthType.SERVICE_ACCOUNT

    def __init__(self: "ServiceAccountAuth", credentials: Credentials) -> None:
        self._google_creds = None
        self._token_refreshed_at: Optional[float] = None
        super().__init__(credentials)

    @property
    def auth_type(self: "ServiceAccountAuth") -> AuthType:
        return AuthType.SERVICE_ACCOUNT

    @property
    def is_refreshable(self: "ServiceAccountAuth") -> bool:
        return True

    @property
    def token(self: "ServiceAccountAuth") -> Optional[str]:
        if self._google_creds and hasattr(self._google_creds, 'token'):
            return self._google_creds.token
        return None

    def _load_google_credentials(self: "ServiceAccountAuth") -> None:
        """加载 Google 服务账号凭证"""
        try:
            from google.oauth2 import service_account

            if self._credentials.service_account_info:
                self._google_creds = (
                    service_account.Credentials.from_service_account_info(
                        self._credentials.service_account_info,
                        scopes=self._credentials.scopes or None,
                    )
                )
            elif self._credentials.service_account_file:
                self._google_creds = (
                    service_account.Credentials.from_service_account_file(
                        self._credentials.service_account_file,
                        scopes=self._credentials.scopes or None,
                    )
                )
            else:
                raise AuthError("未提供服务账号信息")

        except ImportError as e:
            raise AuthError(
                "google-auth 库未安装。请执行: pip install google-auth"
            ) from e

    def _ensure_token(self: "ServiceAccountAuth") -> None:
        """确保 Access Token 已获取且未过期"""
        if not self._google_creds:
            self._load_google_credentials()

        if not self._google_creds.valid or self._credentials.needs_refresh:
            from google.auth.transport.requests import (
                Request as GoogleRequest,
            )

            self._google_creds.refresh(GoogleRequest())
            self._token_refreshed_at = time.time()
            self._credentials.access_token = self._google_creds.token
            if self._google_creds.expiry:
                self._credentials.token_expiry = (
                    self._google_creds.expiry.timestamp()
                )

    def get_auth_header(self: "ServiceAccountAuth") -> Dict[str, str]:
        try:
            self._ensure_token()
            return {"Authorization": f"Bearer {self.token}"}
        except AuthError:
            return {}

    def apply_auth(self: "ServiceAccountAuth", request: Any) -> Any:
        try:
            self._ensure_token()
            if self.token:
                request.headers["Authorization"] = f"Bearer {self.token}"
        except AuthError as e:
            logger.error("服务账号认证应用失败: %s", e)
        return request

    def validate(self: "ServiceAccountAuth") -> bool:
        """验证服务账号凭证（本地加载，不调用远端 API）"""
        try:
            self._load_google_credentials()
            self._update_validation(True)
            return True
        except AuthError as e:
            logger.error("服务账号验证失败: %s", e)
            self._update_validation(False, str(e))
            return False

    def refresh(self: "ServiceAccountAuth") -> bool:
        """刷新服务账号 Access Token"""
        try:
            if not self._google_creds:
                self._load_google_credentials()
            from google.auth.transport.requests import (
                Request as GoogleRequest,
            )

            self._google_creds.refresh(GoogleRequest())
            self._credentials.access_token = self._google_creds.token
            if self._google_creds.expiry:
                self._credentials.token_expiry = (
                    self._google_creds.expiry.timestamp()
                )
            self._token_refreshed_at = time.time()
            self._update_validation(True)
            logger.info("服务账号 Access Token 刷新成功")
            return True
        except Exception as e:
            logger.error("服务账号 Token 刷新失败: %s", e)
            self._update_validation(False, f"刷新失败: {str(e)}")
            raise AuthRefreshError(
                f"服务账号 Token 刷新失败: {e}"
            ) from e

    def to_summary(self: "ServiceAccountAuth") -> Dict[str, Any]:
        client_email = "unknown"
        if self._google_creds and hasattr(
            self._google_creds, "service_account_email"
        ):
            client_email = self._google_creds.service_account_email
        return {
            "type": "service_account",
            "client_email": client_email,
            "scopes": self._credentials.scopes,
            "service_account_file": self._credentials.service_account_file,
            "is_valid": self._credentials.is_valid,
            "is_refreshable": self.is_refreshable,
            "token_expiry": self._credentials.token_expiry,
            "days_until_expiry": (
                round(self._credentials.days_until_expiry, 1)
                if self._credentials.days_until_expiry
                else None
            ),
        }


# ============================================================================
# OAuth2Auth — OAuth2 认证
# ============================================================================


class OAuth2Auth(AuthStrategy):
    """
    OAuth2 认证策略 — Authorization Code Flow

    适用服务: Google Workspace (OAuth2 方式)
    特性: 支持自动刷新（使用 Refresh Token）
    """

    _auth_type = AuthType.OAUTH2
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    TOKEN_INFO_ENDPOINT = "https://oauth2.googleapis.com/tokeninfo"

    def __init__(self: "OAuth2Auth", credentials: Credentials) -> None:
        self._refresh_lock = False
        super().__init__(credentials)

    @property
    def auth_type(self: "OAuth2Auth") -> AuthType:
        return AuthType.OAUTH2

    @property
    def is_refreshable(self: "OAuth2Auth") -> bool:
        return bool(self._credentials.refresh_token)

    def get_auth_header(self: "OAuth2Auth") -> Dict[str, str]:
        access_token = self._credentials.access_token
        if not access_token:
            return {}
        return {"Authorization": f"Bearer {access_token}"}

    def apply_auth(self: "OAuth2Auth", request: Any) -> Any:
        access_token = self._credentials.access_token
        if access_token:
            request.headers["Authorization"] = f"Bearer {access_token}"
        return request

    def validate(self: "OAuth2Auth") -> bool:
        """验证 Access Token 是否有效"""
        if not self._session:
            self._update_validation(False, "requests 库未安装")
            return False

        if (
            self._credentials.is_expired
            and not self._credentials.refresh_token
        ):
            self._update_validation(
                False, "Access Token 已过期且没有 Refresh Token"
            )
            return False

        if (
            self._credentials.refresh_token
            and self._credentials.needs_refresh
        ):
            logger.info("Access Token 即将过期，尝试自动刷新")
            if self.refresh():
                return True

        if not self._credentials.access_token:
            self._update_validation(False, "Access Token 为空")
            return False

        try:
            resp = self._session.get(
                self.TOKEN_INFO_ENDPOINT,
                params={"access_token": self._credentials.access_token},
                timeout=10,
            )

            if resp.status_code == 200:
                data = resp.json()
                expiry_timestamp = data.get("exp")
                if expiry_timestamp:
                    self._credentials.token_expiry = float(expiry_timestamp)
                self._update_validation(True)
                return True

            if resp.status_code in (400, 401):
                if self._credentials.refresh_token and self.refresh():
                    return True
                self._update_validation(False, "Access Token 无效")
                return False

            self._update_validation(
                False, f"Token 验证返回异常: HTTP {resp.status_code}"
            )
            return False

        except Exception as e:
            logger.error("OAuth2 验证网络请求失败: %s", e)
            self._update_validation(False, f"网络请求失败: {str(e)}")
            return False

    def refresh(self: "OAuth2Auth") -> bool:
        """使用 Refresh Token 获取新的 Access Token"""
        if not self._session:
            return False

        if not self._credentials.refresh_token:
            logger.error("没有 Refresh Token，无法刷新")
            return False

        if self._refresh_lock:
            logger.warning("刷新操作正在进行中")
            return False

        self._refresh_lock = True
        try:
            refresh_secret = ""
            if self._credentials.refresh_token:
                refresh_secret = (
                    self._credentials.refresh_token.get_secret_value()
                )

            client_secret = ""
            if self._credentials.client_secret:
                client_secret = (
                    self._credentials.client_secret.get_secret_value()
                )

            resp = self._session.post(
                self.TOKEN_ENDPOINT,
                data={
                    "client_id": self._credentials.client_id or "",
                    "client_secret": client_secret,
                    "refresh_token": refresh_secret,
                    "grant_type": "refresh_token",
                },
                timeout=15,
            )

            if resp.status_code == 200:
                data = resp.json()
                self._credentials.access_token = data["access_token"]
                expires_in = data.get("expires_in", 3600)
                self._credentials.token_expiry = time.time() + expires_in
                self._update_validation(True)
                logger.info("OAuth2 Access Token 刷新成功")
                return True

            error_detail = resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
            logger.error("OAuth2 Token 刷新失败: %s", error_detail)
            self._update_validation(
                False, f"Token 刷新失败: {error_detail}"
            )
            return False

        except Exception as e:
            raise AuthRefreshError(f"Token 刷新网络请求失败: {e}") from e
        finally:
            self._refresh_lock = False

    def to_summary(self: "OAuth2Auth") -> Dict[str, Any]:
        client_id_prefix = None
        if self._credentials.client_id:
            cid = self._credentials.client_id
            client_id_prefix = cid[:8] + "..." if len(cid) > 8 else "***"
        return {
            "type": "oauth2",
            "client_id_prefix": client_id_prefix,
            "scopes": self._credentials.scopes,
            "is_valid": self._credentials.is_valid,
            "is_refreshable": self.is_refreshable,
            "token_expiry": self._credentials.token_expiry,
            "days_until_expiry": (
                round(self._credentials.days_until_expiry, 1)
                if self._credentials.days_until_expiry
                else None
            ),
        }


# ============================================================================
# CredentialMonitor — 凭证过期检测与健康监控
# ============================================================================


AlertCallback = Callable[[CredentialHealth], None]


class CredentialMonitor:
    """
    凭证健康监控器

    使用守护线程执行后台监控:
    1. 定期检测所有凭证的有效性
    2. 检测即将过期的凭证（默认7天预警）
    3. 支持自定义告警回调
    4. 自动刷新可刷新的凭证
    5. 提供完整的健康报告
    """

    def __init__(
        self,
        auth_manager: "AuthManager",
        check_interval: int = 3600,
        expiry_warning_days: int = 7,
        auto_refresh: bool = True,
    ) -> None:
        """
        初始化凭证健康监控器

        Args:
            auth_manager: 认证管理器实例
            check_interval: 检查间隔（秒），默认 1 小时
            expiry_warning_days: 过期前预警天数
            auto_refresh: 是否自动刷新可刷新的凭证
        """
        self._auth_manager = auth_manager
        self._check_interval = check_interval
        self._expiry_warning_days = expiry_warning_days
        self._auto_refresh = auto_refresh
        self._health_status: Dict[str, CredentialHealth] = {}
        self._alert_callbacks: List[AlertCallback] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._register_default_alerts()

    # ─── 属性 ───

    @property
    def is_running(self: "CredentialMonitor") -> bool:
        """监控线程是否正在运行"""
        return self._thread is not None and self._thread.is_alive()

    @property
    def health_status(self: "CredentialMonitor") -> Dict[str, CredentialHealth]:
        with self._lock:
            return dict(self._health_status)

    # ─── 告警系统 ───

    def _register_default_alerts(self: "CredentialMonitor") -> None:
        self.add_alert_callback(self._console_alert)
        self.add_alert_callback(self._log_alert)

    def add_alert_callback(self: "CredentialMonitor", callback: AlertCallback) -> None:
        """添加自定义告警回调"""
        if callback not in self._alert_callbacks:
            self._alert_callbacks.append(callback)

    def remove_alert_callback(self: "CredentialMonitor", callback: AlertCallback) -> None:
        if callback in self._alert_callbacks:
            self._alert_callbacks.remove(callback)

    def _emit_alert(self: "CredentialMonitor", health: CredentialHealth) -> None:
        """发送告警到所有回调"""
        for callback in self._alert_callbacks:
            try:
                callback(health)
            except Exception as e:
                logger.error("告警回调执行失败: %s", e)

    def _console_alert(self: "CredentialMonitor", health: CredentialHealth) -> None:
        """控制台告警"""
        if health.status == CredentialStatus.EXPIRED:
            print(
                f"\n[紧急] 凭证已过期 - {health.service}: {health.message}"
            )
        elif health.status == CredentialStatus.INVALID:
            print(
                f"\n[紧急] 凭证无效 - {health.service}: {health.message}"
            )
        elif health.status == CredentialStatus.EXPIRING_SOON:
            days = health.days_until_expiry
            day_str = f"{days:.1f} 天" if days is not None else "未知"
            print(
                f"\n[警告] 凭证即将过期 - {health.service}: 还剩 {day_str}"
            )

    def _log_alert(self: "CredentialMonitor", health: CredentialHealth) -> None:
        """日志告警"""
        if health.status == CredentialStatus.EXPIRED:
            logger.error(
                "凭证已过期 [%s]: %s", health.service, health.message
            )
        elif health.status == CredentialStatus.INVALID:
            logger.error(
                "凭证无效 [%s]: %s", health.service, health.message
            )
        elif health.status == CredentialStatus.EXPIRING_SOON:
            logger.warning(
                "凭证即将过期 [%s]: %s", health.service, health.message
            )

    # ─── 检查逻辑 ───

    def check_service(self: "CredentialMonitor", service_name: str) -> CredentialHealth:
        """
        检查指定服务的凭证健康状态

        流程:
            1. 获取服务策略
            2. 执行凭证验证
            3. 检查过期时间
            4. 如需要，尝试自动刷新
            5. 更新健康状态缓存
            6. 如状态不健康，发送告警
        """
        try:
            strategy = self._auth_manager.get_strategy(service_name)
        except Exception:
            health = CredentialHealth(
                service=service_name,
                status=CredentialStatus.NOT_CONFIGURED,
                message="凭证未配置",
            )
            self._update_health(health)
            return health

        summary = strategy.to_summary()

        try:
            is_valid = strategy.validate()
        except AuthValidationError as e:
            logger.error("验证 %s 时发生异常: %s", service_name, e)
            is_valid = False

        status = CredentialStatus.HEALTHY
        message = "凭证有效"
        expires_at = None

        if not is_valid:
            status = CredentialStatus.INVALID
            message = (
                strategy.credentials.validation_error or "凭证验证失败"
            )
        else:
            token_expiry = strategy.credentials.token_expiry
            if token_expiry:
                expires_at = token_expiry
                days_until = strategy.credentials.days_until_expiry
                if days_until is not None:
                    if days_until <= 0:
                        status = CredentialStatus.EXPIRED
                        message = (
                            f"凭证已于 "
                            f"{datetime.fromtimestamp(expires_at)} 过期"
                        )
                    elif days_until <= self._expiry_warning_days:
                        status = CredentialStatus.EXPIRING_SOON
                        message = f"凭证将在 {days_until:.1f} 天后过期"

        # 尝试自动刷新
        refreshed = False
        if status in (CredentialStatus.EXPIRED, CredentialStatus.EXPIRING_SOON):
            if self._auto_refresh and strategy.is_refreshable:
                logger.info("尝试自动刷新 %s 凭证", service_name)
                try:
                    if strategy.refresh():
                        is_valid = strategy.validate()
                        if is_valid:
                            status = CredentialStatus.HEALTHY
                            message = "凭证已自动刷新"
                            expires_at = strategy.credentials.token_expiry
                            refreshed = True
                        else:
                            status = CredentialStatus.INVALID
                            message = "刷新后验证仍失败"
                    else:
                        message += "（自动刷新失败）"
                except AuthRefreshError as e:
                    logger.error("自动刷新 %s 凭证失败: %s", service_name, e)
                    message += f"（自动刷新异常: {e}）"

        health = CredentialHealth(
            service=service_name,
            status=status,
            last_checked=time.time(),
            expires_at=expires_at,
            message=message,
            details={
                **summary,
                "auto_refreshed": refreshed,
                "is_refreshable": strategy.is_refreshable,
            },
        )

        self._update_health(health)

        if status != CredentialStatus.HEALTHY:
            self._emit_alert(health)

        return health

    def check_all(self: "CredentialMonitor") -> Dict[str, CredentialHealth]:
        """检查所有服务的凭证健康状态"""
        services = self._auth_manager.list_services()
        for service in services:
            try:
                self.check_service(service)
            except Exception as e:
                logger.error("检查 %s 凭证时出错: %s", service, e)
                self._update_health(
                    CredentialHealth(
                        service=service,
                        status=CredentialStatus.UNKNOWN,
                        message=f"检查失败: {str(e)}",
                    )
                )
        return self.health_status

    def _update_health(self: "CredentialMonitor", health: CredentialHealth) -> None:
        with self._lock:
            self._health_status[health.service] = health

    # ─── 报告 ───

    def get_health_report(self: "CredentialMonitor") -> HealthReport:
        """获取完整的健康报告"""
        with self._lock:
            services_dict = {
                name: health.to_report_dict()
                for name, health in self._health_status.items()
            }

            return HealthReport(
                total_services=len(self._health_status),
                healthy=sum(
                    1
                    for h in self._health_status.values()
                    if h.status == CredentialStatus.HEALTHY
                ),
                expiring_soon=sum(
                    1
                    for h in self._health_status.values()
                    if h.status == CredentialStatus.EXPIRING_SOON
                ),
                expired=sum(
                    1
                    for h in self._health_status.values()
                    if h.status == CredentialStatus.EXPIRED
                ),
                invalid_count=sum(
                    1
                    for h in self._health_status.values()
                    if h.status == CredentialStatus.INVALID
                ),
                not_configured=sum(
                    1
                    for h in self._health_status.values()
                    if h.status == CredentialStatus.NOT_CONFIGURED
                ),
                services=services_dict,
            )

    # ─── 后台监控线程 ───

    def start(self: "CredentialMonitor") -> None:
        """启动后台监控线程（守护线程）"""
        if self.is_running:
            logger.warning("监控线程已在运行")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True
        )
        self._thread.start()
        logger.info("凭证监控已启动，检查间隔: %d秒", self._check_interval)

    def stop(self: "CredentialMonitor") -> None:
        """停止后台监控线程"""
        if not self.is_running:
            return

        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                logger.warning("监控线程未在超时时间内停止")
        self._thread = None
        logger.info("凭证监控已停止")

    def _monitor_loop(self: "CredentialMonitor") -> None:
        """监控循环（在线程中运行）"""
        logger.info("监控线程启动，执行首次检查")

        try:
            self.check_all()
        except Exception as e:
            logger.error("首次检查异常: %s", e)

        while not self._stop_event.is_set():
            interrupted = self._stop_event.wait(self._check_interval)
            if interrupted:
                break

            try:
                logger.debug("执行定时凭证健康检查")
                self.check_all()
            except Exception as e:
                logger.error("定时检查异常: %s", e)

        logger.info("监控线程结束")

    def __enter__(self: "CredentialMonitor") -> "CredentialMonitor":
        self.start()
        return self

    def __exit__(self: "CredentialMonitor", exc_type, exc_val, exc_tb: Any | None) -> None:
        self.stop()

    def __repr__(self: "CredentialMonitor") -> str:
        status = "running" if self.is_running else "stopped"
        return (
            f"<CredentialMonitor(status={status}, "
            f"services={len(self._health_status)})>"
        )


# ============================================================================
# AuthManager — 认证管理器主类
# ============================================================================


class AuthManager:
    """
    统一认证管理器

    管理所有服务的认证凭证，提供统一的认证接口。
    支持从配置自动初始化、安全存储集成、健康监控。

    使用示例:
        >>> auth_mgr = AuthManager(config)
        >>> auth_mgr.initialize()
        >>> strategy = auth_mgr.get_strategy("kling")
        >>> headers = strategy.get_auth_header()
    """

    # 认证类型到策略类的映射
    _STRATEGY_REGISTRY: Dict[AuthType, Type[AuthStrategy]] = {
        AuthType.API_KEY: APIKeyAuth,
        AuthType.COOKIE: CookieAuth,
        AuthType.SERVICE_ACCOUNT: ServiceAccountAuth,
        AuthType.OAUTH2: OAuth2Auth,
    }

    # 服务默认配置
    SERVICE_DEFAULTS: Dict[str, Dict[str, Any]] = {
        "gemini": {
            "type": "cookie",
            "endpoint": "https://gemini.google.com",
            "credentials": {"cookie_name": "__Secure-1PSID"},
        },
        "google_workspace": {
            "type": "service_account",
            "endpoint": "https://www.googleapis.com",
            "scopes": [
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/documents.readonly",
                "https://www.googleapis.com/auth/gmail.readonly",
            ],
        },
        "jimeng": {
            "type": "api_key",
            "endpoint": "https://ark.cn-beijing.volces.com",
        },
        "kling": {
            "type": "api_key",
            "endpoint": "https://api.klingai.com",
        },
        "vidu": {
            "type": "api_key",
            "endpoint": "https://api.vidu.com",
        },
        "veo3": {
            "type": "api_key",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta",
        },
    }

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        初始化认证管理器

        Args:
            config: 认证配置字典
        """
        self._config = config or {}
        self._strategies: Dict[str, AuthStrategy] = {}
        self._credentials: Dict[str, Credentials] = {}
        self._lock = threading.RLock()
        self._monitor: Optional[CredentialMonitor] = None

    # ─── 初始化和生命周期 ───

    def initialize(self: "AuthManager") -> "AuthManager":
        """
        初始化所有服务的认证策略

        从配置中读取各服务的认证信息，创建对应的策略实例。

        Returns:
            self（支持链式调用）
        """
        with self._lock:
            auth_configs = self._config.get("auth", {})
            for service_name, service_config in auth_configs.items():
                if service_name.startswith("_"):
                    continue
                if isinstance(service_config, dict) and not service_config.get(
                    "enabled", True
                ):
                    logger.info("服务 %s 已禁用，跳过", service_name)
                    continue
                try:
                    self._init_service(service_name, service_config)
                except AuthNotConfiguredError:
                    logger.info(
                        "服务 %s 未配置凭证，跳过", service_name
                    )
                except AuthError as e:
                    logger.error(
                        "服务 %s 认证初始化失败: %s", service_name, e
                    )
                except Exception as e:
                    logger.error(
                        "服务 %s 认证初始化异常: %s", service_name, e
                    )

            logger.info(
                "认证管理器初始化完成: %d 个服务已配置",
                len(self._strategies),
            )
            return self

    def _init_service(
        self, service_name: str, service_config: Dict[str, Any]
    ) -> None:
        """初始化单个服务的认证策略"""
        merged_config = self._merge_defaults(service_name, service_config)
        credentials = self._build_credentials(service_name, merged_config)
        if not credentials:
            raise AuthNotConfiguredError(
                f"服务 {service_name} 未配置有效凭证"
            )

        strategy_class = self._STRATEGY_REGISTRY.get(credentials.auth_type)
        if not strategy_class:
            raise AuthError(
                f"不支持的认证类型: {credentials.auth_type.value}"
            )

        if credentials.auth_type == AuthType.API_KEY:
            strategy = strategy_class(credentials, service_name=service_name)
        else:
            strategy = strategy_class(credentials)

        self._credentials[service_name] = credentials
        self._strategies[service_name] = strategy
        logger.debug(
            "服务 %s 认证策略已注册: %s", service_name, strategy
        )

    def _merge_defaults(
        self, service_name: str, user_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """合并用户配置与服务默认配置"""
        defaults = self.SERVICE_DEFAULTS.get(service_name, {})
        merged = {**defaults, **user_config}
        if (
            "credentials" in defaults
            and "credentials" in user_config
        ):
            merged["credentials"] = {
                **defaults["credentials"],
                **user_config["credentials"],
            }
        return merged

    def _build_credentials(
        self, service_name: str, config: Dict[str, Any]
    ) -> Optional[Credentials]:
        """从配置构建 Credentials 模型"""
        auth_type_str = config.get("type", "api_key")
        try:
            auth_type = AuthType(auth_type_str)
        except ValueError:
            logger.warning("未知的认证类型: %s", auth_type_str)
            return None

        credentials_dict = config.get("credentials", {})
        endpoint = config.get("endpoint")
        scopes = config.get("scopes", [])

        # 构建 Credentials
        creds_data: Dict[str, Any] = {
            "auth_type": auth_type,
            "endpoint": endpoint,
            "scopes": scopes,
        }

        if auth_type == AuthType.API_KEY:
            api_key = credentials_dict.get("api_key")
            if not api_key:
                return None
            creds_data["api_key"] = api_key
        elif auth_type == AuthType.COOKIE:
            cookie_value = credentials_dict.get("cookie_value")
            if not cookie_value:
                return None
            creds_data["cookie_name"] = credentials_dict.get(
                "cookie_name", "__Secure-1PSID"
            )
            creds_data["cookie_value"] = cookie_value
        elif auth_type == AuthType.SERVICE_ACCOUNT:
            sa_file = credentials_dict.get("service_account_file")
            sa_info = credentials_dict.get("service_account_info")
            if not sa_file and not sa_info:
                return None
            creds_data["service_account_file"] = sa_file
            creds_data["service_account_info"] = sa_info
        elif auth_type == AuthType.OAUTH2:
            access_token = credentials_dict.get("access_token")
            refresh_token = credentials_dict.get("refresh_token")
            if not access_token and not refresh_token:
                return None
            creds_data.update({
                "client_id": credentials_dict.get("client_id"),
                "client_secret": credentials_dict.get("client_secret"),
                "refresh_token": refresh_token,
                "access_token": access_token,
                "token_expiry": credentials_dict.get("token_expiry"),
            })

        return Credentials(**creds_data)

    # ─── 策略访问 ───

    def get_strategy(self: "AuthManager", service: str) -> AuthStrategy:
        """
        获取指定服务的认证策略

        Args:
            service: 服务名称

        Returns:
            AuthStrategy 实例

        Raises:
            AuthNotFoundError: 服务未配置认证
        """
        with self._lock:
            strategy = self._strategies.get(service)
            if not strategy:
                raise AuthNotFoundError(
                    f"服务 '{service}' 未配置认证。"
                    f"已配置: {list(self._strategies.keys())}"
                )
            return strategy

    def get_auth_header(self: "AuthManager", service: str) -> Dict[str, str]:
        """获取指定服务的认证头（便捷方法）"""
        return self.get_strategy(service).get_auth_header()

    def apply_auth(self: "AuthManager", service: str, request: Any) -> Any:
        """将认证应用到请求（便捷方法）"""
        return self.get_strategy(service).apply_auth(request)

    def get_credentials(self: "AuthManager", service: str) -> Credentials:
        """获取指定服务的凭证数据"""
        with self._lock:
            if service not in self._credentials:
                raise AuthNotFoundError(
                    f"服务 '{service}' 未配置认证"
                )
            return self._credentials[service]

    # ─── 验证和刷新 ───

    def validate(self: "AuthManager", service: str) -> bool:
        """验证指定服务的凭证"""
        try:
            return self.get_strategy(service).validate()
        except Exception as e:
            logger.error("验证 %s 凭证时出错: %s", service, e)
            return False

    def validate_all(self: "AuthManager") -> Dict[str, bool]:
        """验证所有已配置服务的凭证"""
        with self._lock:
            results: Dict[str, bool] = {}
            for service_name, strategy in self._strategies.items():
                try:
                    results[service_name] = strategy.validate()
                except Exception as e:
                    logger.error(
                        "验证 %s 凭证时出错: %s", service_name, e
                    )
                    results[service_name] = False
            return results

    def refresh(self: "AuthManager", service: str) -> bool:
        """刷新指定服务的凭证"""
        strategy = self.get_strategy(service)
        if not strategy.is_refreshable:
            logger.warning(
                "服务 %s 的认证类型不支持自动刷新", service
            )
            return False
        return strategy.refresh()

    def refresh_expired(self: "AuthManager") -> Dict[str, bool]:
        """
        刷新所有已过期的凭证

        Returns:
            {service_name: refresh_success}
        """
        with self._lock:
            results: Dict[str, bool] = {}
            for service_name, strategy in self._strategies.items():
                if not strategy.is_refreshable:
                    continue
                if (
                    strategy.is_expired
                    or strategy.credentials.needs_refresh
                ):
                    try:
                        results[service_name] = strategy.refresh()
                    except Exception as e:
                        logger.error(
                            "刷新 %s 凭证时出错: %s", service_name, e
                        )
                        results[service_name] = False
            return results

    # ─── 查询和摘要 ───

    def list_services(self: "AuthManager") -> List[str]:
        """列出所有已配置认证的服务名称"""
        with self._lock:
            return list(self._strategies.keys())

    def get_summary(self: "AuthManager") -> Dict[str, Dict[str, Any]]:
        """获取所有凭证的脱敏摘要"""
        with self._lock:
            return {
                name: strategy.to_summary()
                for name, strategy in self._strategies.items()
            }

    def is_configured(self: "AuthManager", service: str) -> bool:
        """检查服务是否已配置认证"""
        return service in self._strategies

    def is_healthy(self: "AuthManager", service: str) -> bool:
        """检查服务凭证是否健康（有效且未过期）"""
        try:
            strategy = self.get_strategy(service)
            return strategy.is_valid and not strategy.is_expired
        except AuthNotFoundError:
            return False

    # ─── 动态注册和注销 ───

    def register(
        self,
        service: str,
        credentials: Credentials,
        replace: bool = False,
    ) -> None:
        """
        动态注册服务的认证策略

        Args:
            service: 服务名称
            credentials: 凭证数据
            replace: 是否替换已有配置

        Raises:
            AuthError: 服务已配置且 replace=False
        """
        with self._lock:
            if service in self._strategies and not replace:
                raise AuthError(
                    f"服务 {service} 已配置认证，"
                    f"如需替换请设置 replace=True"
                )

            strategy_class = self._STRATEGY_REGISTRY.get(
                credentials.auth_type
            )
            if not strategy_class:
                raise AuthError(
                    f"不支持的认证类型: {credentials.auth_type.value}"
                )

            if credentials.auth_type == AuthType.API_KEY:
                strategy = strategy_class(credentials, service_name=service)
            else:
                strategy = strategy_class(credentials)

            self._credentials[service] = credentials
            self._strategies[service] = strategy
            logger.info(
                "服务 %s 认证策略已%s",
                service,
                "替换" if replace else "注册",
            )

    def unregister(self: "AuthManager", service: str) -> None:
        """注销服务的认证策略"""
        with self._lock:
            self._strategies.pop(service, None)
            self._credentials.pop(service, None)
            logger.info("服务 %s 认证策略已注销", service)

    # ─── 监控集成 ───

    def start_monitor(
        self,
        check_interval: int = 3600,
        expiry_warning_days: int = 7,
        auto_refresh: bool = True,
        alert_callbacks: Optional[List[Any]] = None,
    ) -> CredentialMonitor:
        """
        启动凭证健康监控

        Args:
            check_interval: 检查间隔（秒），默认 1 小时
            expiry_warning_days: 过期前预警天数
            auto_refresh: 是否自动刷新可刷新的凭证
            alert_callbacks: 自定义告警回调函数列表

        Returns:
            CredentialMonitor 实例
        """
        if self._monitor and self._monitor.is_running:
            logger.warning("监控已在运行，停止旧实例")
            self._monitor.stop()

        self._monitor = CredentialMonitor(
            auth_manager=self,
            check_interval=check_interval,
            expiry_warning_days=expiry_warning_days,
            auto_refresh=auto_refresh,
        )

        if alert_callbacks:
            for callback in alert_callbacks:
                self._monitor.add_alert_callback(callback)

        self._monitor.start()
        logger.info("凭证健康监控已启动")
        return self._monitor

    def stop_monitor(self: "AuthManager") -> None:
        """停止凭证健康监控"""
        if self._monitor:
            self._monitor.stop()
            self._monitor = None
            logger.info("凭证健康监控已停止")

    def get_monitor(self: "AuthManager") -> Optional[CredentialMonitor]:
        """获取监控器实例"""
        return self._monitor

    # ─── 上下文管理器 ───

    def __enter__(self: "AuthManager") -> "AuthManager":
        if not self._strategies:
            self.initialize()
        return self

    def __exit__(self: "AuthManager", exc_type, exc_val, exc_tb: Any | None) -> None:
        self.stop_monitor()
        for strategy in self._strategies.values():
            if hasattr(strategy, "_session") and strategy._session:
                strategy._session.close()

    def __repr__(self: "AuthManager") -> str:
        return f"<AuthManager(services={list(self._strategies.keys())})>"


# ============================================================================
# 模块导出
# ============================================================================

__all__ = [
    # 枚举
    "AuthType",
    "CredentialStatus",
    # 数据模型
    "Credentials",
    "CredentialHealth",
    "HealthReport",
    # 异常
    "AuthError",
    "AuthNotFoundError",
    "AuthNotConfiguredError",
    "AuthValidationError",
    "AuthRefreshError",
    # 策略基类
    "AuthStrategy",
    # 策略实现
    "CookieAuth",
    "APIKeyAuth",
    "ServiceAccountAuth",
    "OAuth2Auth",
    # 监控
    "CredentialMonitor",
    # 管理器
    "AuthManager",
]

# 需要延迟导入的 os 模块
import os
