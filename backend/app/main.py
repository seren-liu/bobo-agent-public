# ============================================================
# 第一部分：导入依赖
# ============================================================
# Python 标准库
import json                    # JSON 序列化/反序列化
import logging                 # 日志记录
import time                    # 计时（计算请求耗时）
from contextlib import AsyncExitStack      # 异步上下文管理器栈（管理多个资源生命周期）
from contextlib import asynccontextmanager  # 把异步函数变成上下文管理器的装饰器
from uuid import uuid4         # 生成唯一 ID

# FastAPI 相关
from fastapi import FastAPI, Request  # FastAPI 应用类、请求对象
from fastapi.middleware.cors import CORSMiddleware  # 跨域中间件
from jose import JWTError      # JWT 解码错误类型
from starlette.middleware.base import BaseHTTPMiddleware  # 自定义中间件的基类
from starlette.responses import JSONResponse, Response  # JSON 响应、通用响应

# 项目内部模块（从 app. 开头的都是本项目自己的代码）
# 导入 6 个路由组（相当于 Spring 的 6 个 Controller）
from app.api.auth import router as auth_router      # 认证相关：登录、注册、刷新 token
from app.api.agent import router as agent_router    # AI 对话接口
from app.api.memory import router as memory_router  # 用户记忆/画像接口
from app.api.menu import router as menu_router      # 菜单查询接口
from app.api.records import router as records_router  # 饮品记录接口
from app.api.vision import router as vision_router    # 视觉识别接口

# 其他内部模块
from app.agent.graph import close_agent_runtime   # Agent 运行时清理函数
from app.core.authz import normalize_capabilities, reset_auth_context, set_auth_context  # 权限管理
from app.core.config import get_settings           # 获取配置对象（单例）
from app.core.logging import configure_logging, reset_log_context, set_log_context  # 日志上下文
from app.core.security import decode_token         # JWT 解码函数
from app.models.db import close_pool, init_pool    # 数据库连接池初始化/关闭
from app.observability import metrics_content_type, metrics_payload, observe_http_request  # Prometheus 指标
from app.tools.mcp_server import create_mcp_server  # MCP 服务创建函数

# ============================================================
# 第二部分：全局初始化
# ============================================================
# 配置日志格式（在模块加载时就执行，只执行一次）
configure_logging()

# 获取当前模块的 logger 对象，后续用 logger.info() 记录日志
# __name__ 是 Python 内置变量，值是 "app.main"
logger = logging.getLogger(__name__)


# ============================================================
# 第三部分：辅助函数
# ============================================================

def _is_mcp_service_token(token: str, settings) -> bool:
    """
    判断一个 token 是否是 MCP 服务的内部调用 token。
    
    为什么需要这个？
    - Agent 调用工具时，会通过 MCP 协议发请求到 /mcp 路径
    - 这些请求不是来自用户，而是来自 Agent 内部
    - 所以需要一个特殊的 token 来标识这种"内部调用"
    
    参数:
        token: 请求头里带的 token 字符串
        settings: 配置对象
    
    返回:
        True = 这是 MCP 内部调用，给予全部权限
        False = 这不是 MCP token，需要按普通用户 token 处理
    """
    # 候选 token 集合（用集合是因为查找快，O(1)）
    candidates = {f"{settings.jwt_secret}:mcp"}  # 默认：JWT密钥 + ":mcp" 拼接
    
    # 如果配置了专门的 MCP token，也加入候选
    if settings.mcp_service_token:
        candidates.add(settings.mcp_service_token)
    
    # 判断传入的 token 是否在候选集合中
    return token in candidates


# ============================================================
# 第四部分：JWT 认证中间件（核心！）
# ============================================================
# 中间件是什么？
# - 可以理解为"门卫"：所有请求进来都要先经过中间件
# - 中间件可以在请求到达路由处理函数之前做事情（比如检查登录状态）
# - 也可以在响应返回之前做事情（比如记录日志）

class JWTMiddleware(BaseHTTPMiddleware):
    """
    JWT 认证中间件：验证每个请求的登录状态。
    
    继承 BaseHTTPMiddleware 后，只需要实现 dispatch() 方法。
    dispatch() 会在每个请求进来时自动被调用。
    """
    
    # 白名单：这些路径不需要登录就能访问
    # 用集合（set）是因为查找快，O(1) 时间复杂度
    EXCLUDED = {
        "/bobo/auth/login",       # 登录接口
        "/bobo/auth/register",    # 注册接口
        "/bobo/auth/refresh",     # 刷新 token 接口
        "/bobo/health",           # 健康检查（给运维用的）
        "/metrics",               # Prometheus 指标接口
        "/docs",                  # API 文档
        "/openapi.json",          # OpenAPI 规范文件
        "/redoc",                 # 另一种 API 文档风格
    }

    async def dispatch(self, request: Request, call_next):
        """
        中间件的核心方法，每个请求都会执行。
        
        参数:
            request: 当前请求对象，包含请求头、路径、方法等信息
            call_next: 一个函数，调用它会继续执行后续逻辑（下一个中间件或路由处理函数）
        
        返回:
            Response 对象，返回给客户端
        
        执行流程:
            1. 请求前：生成请求 ID、记录开始时间
            2. 检查是否需要认证（白名单？OPTIONS 请求？）
            3. 验证 token
            4. 调用 call_next() 继续处理请求
            5. 响应后：附加请求 ID、记录日志
        """
        # -------- 第一步：准备请求级别的上下文 --------
        
        settings = get_settings()  # 获取配置对象（单例，每次调用返回同一个）
        
        # 生成或获取请求 ID
        # 如果请求头带了 X-Request-Id，就用它（方便前端追踪）
        # 否则生成一个随机的 32 位十六进制字符串
        request_id = request.headers.get("X-Request-Id") or uuid4().hex
        
        # 把请求 ID 存到 request.state 上
        # request.state 是一个可以自由附加属性的对象
        # 后续的路由处理函数可以通过 request.state.request_id 拿到这个值
        request.state.request_id = request_id
        
        # 记录请求开始时间（用于计算耗时）
        # perf_counter() 返回高精度时间戳，比 time.time() 更准确
        start = time.perf_counter()
        
        # 认证上下文的 token（后面会用到，先初始化为 None）
        auth_context_token = None
        
        # 设置日志上下文：把请求 ID、方法、路径注入到日志中
        # 这样后续所有 logger.info() 都会自动带上这些信息
        # 返回值是一个 token，用于最后清理上下文
        log_context_tokens = set_log_context(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        # -------- 第二步：定义一个辅助函数 --------
        
        def _attach_metadata(response):
            """
            在响应上附加元数据（请求 ID、耗时），并记录日志。
            这个函数会在返回响应之前被调用。
            
            参数:
                response: 要返回给客户端的响应对象
            返回:
                处理后的响应对象
            """
            # 1. 在响应头里加上请求 ID
            # 前端可以通过这个 ID 在日志中追踪请求
            response.headers["X-Request-Id"] = request_id
            
            # 2. 计算请求耗时（毫秒）
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            
            # 3. 获取路由路径（用于监控指标）
            # request.scope["route"].path 是路由定义的路径模板
            # 比如 /records/{id} 而不是 /records/123
            route = getattr(request.scope.get("route"), "path", None) or request.url.path or "unknown"
            
            # 4. 记录 Prometheus 监控指标
            # 这些指标会被 Grafana 抓取，用于监控服务性能
            observe_http_request(
                method=request.method,
                route=route,
                status_code=int(getattr(response, "status_code", 0) or 0),
                duration_seconds=max(duration_ms / 1000.0, 0.0),
            )
            
            # 5. 记录结构化 JSON 日志
            # 用 JSON 格式是为了方便 ELK/Grafana 解析和检索
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": getattr(response, "status_code", None),
                        "duration_ms": duration_ms,
                    },
                    ensure_ascii=False,  # 中文不转义，保持可读
                    default=str,          # 非标准类型（如 datetime）转为字符串
                )
            )
            return response

        # -------- 第三步：判断是否需要认证 --------
        
        # 获取请求路径
        path = request.url.path
        
        # root_path 是子应用挂载时的前缀
        # 比如 MCP 服务挂载在 /mcp，那 root_path 就是 /mcp
        root_path = request.scope.get("root_path", "")
        
        # 判断是否是 MCP 请求
        mcp_request = path.startswith("/mcp") or str(root_path).startswith("/mcp")
        
        # 白名单判断：OPTIONS 请求或白名单路径直接放行
        # OPTIONS 是浏览器跨域预检请求，不带 token，必须放行
        # any() 函数：只要有一个条件为 True 就返回 True
        if request.method == "OPTIONS" or any(path.startswith(p) for p in self.EXCLUDED):
            # call_next(request) 会继续执行后续逻辑
            # await 是因为这是个异步操作
            return _attach_metadata(await call_next(request))

        # -------- 第四步：检查 Authorization 请求头 --------
        
        # 获取 Authorization 请求头
        # 格式应该是 "Bearer xxx.yyy.zzz"（Bearer + 空格 + JWT token）
        auth_header = request.headers.get("Authorization", "")
        
        # 如果不是以 "Bearer " 开头，说明格式不对
        if not auth_header.startswith("Bearer "):
            # 返回 401 未授权错误
            # JSONResponse 是手动构造的响应对象（在中间件里不能用 return dict 的便捷方式）
            return _attach_metadata(JSONResponse(status_code=401, content={"detail": "missing bearer token"}))
        
        # 提取 token 部分
        # replace("Bearer ", "", 1) 只替换第一个匹配项
        # "Bearer abc123" -> "abc123"
        token = auth_header.replace("Bearer ", "", 1)
        # -------- 第五步：分支一 - MCP 内部调用 --------
        
        # 如果是 MCP 请求，且 token 是 MCP 服务专用 token
        # 说明这是 Agent 内部调用工具，给予全部权限
        if mcp_request and _is_mcp_service_token(token, settings):
            # 在 request.state 上设置用户信息
            # 后续的处理函数可以通过 request.state.user_id 获取
            request.state.user_id = "mcp-service"
            request.state.auth_source = "mcp_service_token"  # 认证来源
            request.state.auth_capabilities = ("*",)  # 权限列表，"*" 表示全部权限
            
            # 更新日志上下文
            log_context_tokens.update(
                set_log_context(user_id="mcp-service", auth_source="mcp_service_token")
            )
            
            # 设置认证上下文（用于工具调用时的权限检查）
            auth_context_token = set_auth_context(
                {
                    "user_id": "mcp-service",
                    "auth_source": "mcp_service_token",
                    "source": request.headers.get("X-Bobo-Source", "mcp"),
                    "capabilities": ("*",),
                }
            )
            
            # try/finally 确保请求结束后清理上下文
            try:
                return _attach_metadata(await call_next(request))
            finally:
                # 清理认证上下文（避免影响下一个请求）
                if auth_context_token is not None:
                    reset_auth_context(auth_context_token)

        # -------- 第六步：分支二 - 普通用户 JWT token --------
        
        # 解码 JWT token
        # decode_token 会验证签名、过期时间等
        # 如果 token 无效，会抛出 JWTError
        try:
            # 解码 token，获取 payload（载荷，包含用户信息）
            payload = decode_token(token, expected_token_type="access")
            
            # "sub" 是 JWT 标准字段，表示 subject（用户 ID）
            user_id = payload.get("sub")
            
            # 如果 payload 里没有 sub，说明 token 有问题
            if not user_id:
                raise JWTError("missing subject")
            
            # 把用户信息存到 request.state
            request.state.user_id = str(user_id)
            request.state.auth_source = "user_bearer"  # 认证来源：用户 Bearer token
            
            # 从 payload 获取权限列表并规范化
            # normalize_capabilities 会把权限转成标准格式
            request.state.auth_capabilities = normalize_capabilities(payload.get("caps"))
            
            # 更新日志上下文
            log_context_tokens.update(
                set_log_context(user_id=str(user_id), auth_source="user_bearer")
            )
            
            # 设置认证上下文
            auth_context_token = set_auth_context(
                {
                    "user_id": str(user_id),
                    "auth_source": "user_bearer",
                    "source": request.headers.get("X-Bobo-Source", "http"),
                    "capabilities": request.state.auth_capabilities,
                }
            )
            
        except JWTError:
            # token 解码失败，返回 401
            return _attach_metadata(JSONResponse(status_code=401, content={"detail": "invalid token"}))

        # -------- 第七步：继续处理请求并清理上下文 --------
        
        # 走到这里说明认证通过了，继续执行后续逻辑
        try:
            # call_next(request) 会调用下一个中间件或路由处理函数
            # await 等待处理完成，拿到响应
            return _attach_metadata(await call_next(request))
        finally:
            # finally 块无论成功还是失败都会执行
            # 清理认证上下文和日志上下文
            # 这很重要！否则会影响下一个请求
            if auth_context_token is not None:
                reset_auth_context(auth_context_token)
            reset_log_context(log_context_tokens)


# ============================================================
# 第五部分：应用生命周期管理
# ============================================================
# lifespan 是什么？
# - 应用启动时执行初始化逻辑（建立数据库连接、加载模型等）
# - 应用关闭时执行清理逻辑（关闭连接、释放资源等）
# - 类似 Spring 的 @PostConstruct 和 @PreDestroy

@asynccontextmanager  # 装饰器：把这个异步函数变成上下文管理器
async def lifespan(app: FastAPI):
    """
    应用生命周期管理。
    
    yield 之前：启动时执行（初始化资源）
    yield 之后：关闭时执行（释放资源）
    
    用 @asynccontextmanager 装饰后，可以这样用：
        async with lifespan(app):
            # 应用运行中...
    """
    settings = get_settings()
    
    # 从 app.state 获取 MCP 容器（在 create_app 里设置的）
    mcp_container = getattr(app.state, "mcp_container", None)
    
    # AsyncExitStack 用于管理多个异步上下文
    # 可以动态添加多个资源，退出时自动清理
    async with AsyncExitStack() as stack:
        
        # 如果 MCP 子应用有自己的生命周期，也启动它
        if mcp_container and mcp_container.http_app is not None:
            # 获取 MCP 应用的 lifespan 函数
            # 这行代码有点复杂，就是在找 mcp_container.http_app 的 lifespan 属性
            lifespan_context = getattr(
                getattr(mcp_container.http_app, "router", None), 
                "lifespan_context", None
            ) or getattr(mcp_container.http_app, "lifespan", None)
            
            # 如果找到了且是可调用的，就启动它
            if callable(lifespan_context):
                await stack.enter_async_context(lifespan_context(mcp_container.http_app))

        # ========== 启动阶段 ==========
        init_pool()  # 初始化数据库连接池
        logger.info(json.dumps({"event": "app_startup", "app_name": settings.app_name}, ensure_ascii=False))
        
        # yield 是分界线：这里暂停，应用开始运行
        # 当应用关闭时，会从 yield 后面继续执行
        yield
        
        # ========== 关闭阶段 ==========
        await close_agent_runtime()  # 关闭 Agent 运行时
        close_pool()                  # 关闭数据库连接池
        logger.info(json.dumps({"event": "app_shutdown", "app_name": settings.app_name}, ensure_ascii=False))


# ============================================================
# 第六部分：应用工厂函数
# ============================================================
# 为什么用工厂函数而不是直接创建 app？
# 1. 方便测试：每次调用都创建独立的应用实例
# 2. 延迟初始化：配置和资源在函数调用时才加载

def create_app() -> FastAPI:
    """
    创建并配置 FastAPI 应用实例。
    
    这是整个后端的"入口函数"，做了这些事：
    1. 加载配置
    2. 创建 FastAPI 实例
    3. 注册中间件（CORS、JWT）
    4. 注册路由（6 个路由组）
    5. 挂载子应用（MCP 服务）
    
    返回:
        配置好的 FastAPI 应用实例
    """
    # 加载配置
    settings = get_settings()
    
    # 判断是否是生产环境
    # in 操作符检查元素是否在集合中
    is_production = settings.env.lower() in {"prod", "production"}
    
    # 创建 MCP 服务容器
    mcp_container = create_mcp_server()
    
    # 创建 FastAPI 应用实例
    app = FastAPI(
        title=settings.app_name,                          # API 标题
        docs_url=None if is_production else "/docs",     # 生产环境关闭 Swagger 文档
        redoc_url=None if is_production else "/redoc",   # 生产环境关闭 ReDoc 文档
        openapi_url=None if is_production else "/openapi.json",  # 生产环境关闭 OpenAPI 规范
        lifespan=lifespan,                                # 绑定生命周期管理器
    )
    
    # 把 MCP 容器存到 app.state 上
    # lifespan 函数会从这里读取
    app.state.mcp_container = mcp_container

    # 注册 CORS 中间件（跨域）
    # 中间件的执行顺序是：后注册的先执行
    # 所以实际执行顺序是：JWT -> CORS -> 路由
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.cors_prod_origin],  # 生产环境允许的域名
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",  # 本地开发：localhost:任意端口
        allow_credentials=True,   # 允许携带 Cookie 和 Authorization 头
        allow_methods=["*"],      # 允许所有 HTTP 方法（GET、POST、PUT、DELETE 等）
        allow_headers=["*"],      # 允许所有请求头
    )
    
    # 注册 JWT 认证中间件
    app.add_middleware(JWTMiddleware)

    # 注册健康检查接口（不需要认证）
    # @app.get 是装饰器，把函数注册为 GET 接口
    @app.get("/bobo/health")
    def health() -> dict:
        """
        健康检查接口。
        
        给运维/负载均衡器用的，检查服务是否正常运行。
        返回 {"ok": True} 表示服务正常。
        """
        return {"ok": True}

    # 注册 Prometheus 指标接口
    @app.get("/metrics")
    def metrics() -> Response:
        """
        Prometheus 监控指标接口。
        
        返回 Prometheus 格式的指标数据，供 Grafana 抓取。
        """
        return Response(content=metrics_payload(), media_type=metrics_content_type())

    # 注册 6 个路由组
    # include_router 把一个 APIRouter 注册到主应用上
    # 相当于 Spring 的 @RequestMapping 在类级别定义前缀
    app.include_router(auth_router)      # /bobo/auth/*
    app.include_router(records_router)   # /bobo/records/*
    app.include_router(vision_router)    # /bobo/vision/*
    app.include_router(menu_router)      # /bobo/menu/*
    app.include_router(agent_router)     # /bobo/agent/*
    app.include_router(memory_router)    # /bobo/memory/*

    # 挂载 MCP 子应用
    # mount() 把一个完整的 ASGI 应用挂载到某个路径下
    # 所有 /mcp/* 的请求会转发给 mcp_container.http_app 处理
    if mcp_container and mcp_container.http_app is not None:
        app.mount("/mcp", mcp_container.http_app)

    return app


# ============================================================
# 第七部分：模块级别的 app 创建
# ============================================================
# 这行代码在模块被导入时执行
# uvicorn 启动时会导入这个模块，从而创建 app
# 相当于 Spring Boot 的 SpringApplication.run() 启动
app = create_app()
