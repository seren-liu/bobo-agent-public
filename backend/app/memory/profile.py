"""用户画像（Profile）管理模块。

本模块负责用户画像的合并、更新稳定性分类和持久化操作。
用户画像包含多个偏好分区，如饮品偏好、交互偏好、预算偏好等。

核心概念:
- stable（稳定偏好）: 长期有效的偏好，如默认糖度、冰度、品牌偏好
- transient（临时偏好）: 短期有效的偏好，如"最近不想喝太甜"
- unknown（未知稳定性）: 无法判断的偏好更新
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.memory import repository

# 用户画像的分区名称列表，定义了画像的标准结构
_PROFILE_SECTIONS = (
    "display_preferences",
    "drink_preferences",
    "interaction_preferences",
    "budget_preferences",
    "health_preferences",
)

# 稳定偏好字段路径集合
# 这些字段的更新会被分类为 stable，长期有效
_STABLE_FIELD_PATHS = {
    "drink_preferences.default_sugar",      # 默认糖度
    "drink_preferences.default_ice",        # 默认冰度
    "drink_preferences.preferred_brands",    # 偏好品牌列表
    "drink_preferences.preferred_categories", # 偏好饮品类别
    "interaction_preferences.reply_style",  # 回复风格
    "budget_preferences.soft_price_ceiling", # 软性价格上限
    "budget_preferences.price_sensitive",   # 价格敏感度
}

# 临时偏好关键词集合
# 包含这些关键词的更新会被分类为 transient，表示短期有效
_TRANSIENT_HINTS = (
    "最近",   
    "这阵子", 
    "暂时",   
    "先别",   
    "近期",   
    "本周",   
    "今晚",   
    "这周",   
)


def _deep_merge(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """深度合并两个字典，递归处理嵌套结构。

    将 patch 中的值合并到 existing 中，对于嵌套字典会递归合并。
    None 值会被跳过，不会覆盖已有值。

    参数:
        existing: 现有的字典，作为合并基础。
        patch: 要合并的补丁字典。

    返回:
        合并后的新字典，不修改输入参数。
    """
    merged = deepcopy(existing)  # 深拷贝避免修改原对象
    for key, value in patch.items():
        if not isinstance(value, dict):
            # 非字典值直接赋值（跳过 None）
            if value is not None:
                merged[key] = deepcopy(value)
            continue
        # 字典值需要递归合并
        target = merged.get(key)
        if not isinstance(target, dict):
            # 目标位置不是字典，用空字典初始化
            target = {}
        merged[key] = _deep_merge(target, value)
    return merged


def _clean_patch_value(value: Any) -> Any:
    """清理补丁值，移除空值和嵌套的空字典。

    递归清理字典结构，移除值为 None 的键。
    如果清理后字典为空，返回 None。

    参数:
        value: 要清理的值，可以是字典、None 或其他类型。

    返回:
        清理后的值，空字典会变成 None。
    """
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, inner_value in value.items():
            # 递归清理每个值
            cleaned_inner = _clean_patch_value(inner_value)
            if cleaned_inner is not None:
                cleaned[key] = cleaned_inner
        # 空字典返回 None，避免存储无意义的空结构
        return cleaned if cleaned else None
    if value is None:
        return None
    return deepcopy(value)


def merge_profile_patch(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """合并用户画像补丁到现有画像。

    这是 _deep_merge 的公开接口，用于将新的偏好更新合并到现有画像中。

    参数:
        existing: 现有的用户画像字典。
        patch: 要合并的偏好更新补丁。

    返回:
        合并后的用户画像字典。
    """
    return _deep_merge(existing, patch)


def classify_profile_update_stability(field_path: str | None, value: Any, raw_text: str | None = None) -> str:
    """分类画像更新的稳定性。

    根据字段路径、值和原始文本判断更新是稳定偏好还是临时偏好。
    分类逻辑:
    1. 包含临时关键词 -> transient
    2. 在稳定字段列表中 -> stable
    3. interaction_preferences 分区 -> stable
    4. budget_preferences 分区且为数值/布尔 -> stable
    5. 其他情况 -> unknown

    参数:
        field_path: 字段路径，如 "drink_preferences.default_sugar"。
        value: 字段的新值。
        raw_text: 原始用户文本，用于检测临时关键词。

    返回:
        稳定性分类: "stable"、"transient" 或 "unknown"。
    """
    # 拼接所有文本用于临时关键词检测
    text = " ".join(part for part in [field_path or "", str(value or ""), raw_text or ""] if part).strip()
    
    # 检测临时关键词，如"最近"、"暂时"等
    if text and any(hint in text for hint in _TRANSIENT_HINTS):
        return "transient"

    # 检查是否在预定义的稳定字段列表中
    if field_path and field_path in _STABLE_FIELD_PATHS:
        return "stable"

    # interaction_preferences 分区默认为稳定偏好
    if field_path and field_path.startswith("interaction_preferences."):
        return "stable"

    # budget_preferences 分区：数值和布尔值为稳定偏好
    if field_path and field_path.startswith("budget_preferences."):
        return "stable" if isinstance(value, (int, float, bool)) else "unknown"

    # 无法判断的情况
    return "unknown"


def is_profile_update_stable(field_path: str | None, value: Any, raw_text: str | None = None) -> bool:
    """检查画像更新是否为稳定偏好。

    这是 classify_profile_update_stability 的便捷方法，
    直接返回布尔值表示是否为稳定偏好。

    参数:
        field_path: 字段路径。
        value: 字段的新值。
        raw_text: 原始用户文本。

    返回:
        如果是稳定偏好返回 True，否则返回 False。
    """
    return classify_profile_update_stability(field_path, value, raw_text=raw_text) == "stable"


def apply_profile_updates(user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """应用画像更新并持久化到数据库。

    执行流程:
    1. 获取当前画像
    2. 合并更新补丁
    3. 清理空值
    4. 按分区结构化并持久化

    参数:
        user_id: 用户标识符。
        updates: 画像更新字典，可能包含多个分区的更新。

    返回:
        更新后的完整用户画像。
    """
    # 获取当前画像
    current = repository.get_profile(user_id)
    # 深度合并更新
    merged = merge_profile_patch(current, updates)
    # 清理空值并按分区结构化
    payload = {section: _clean_patch_value(merged.get(section, {})) for section in _PROFILE_SECTIONS}
    # 持久化到数据库
    return repository.patch_profile(user_id, payload)


def get_profile(user_id: str) -> dict[str, Any]:
    """获取用户的完整画像。

    参数:
        user_id: 用户标识符。

    返回:
        用户画像字典，包含所有偏好分区。
    """
    return repository.get_profile(user_id)


def patch_profile(user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """应用画像补丁更新。

    这是 apply_profile_updates 的别名，提供更直观的命名。

    参数:
        user_id: 用户标识符。
        patch: 画像更新补丁。

    返回:
        更新后的完整用户画像。
    """
    return apply_profile_updates(user_id, patch)


def derive_profile_candidates_from_stats(user_id: str) -> dict[str, Any]:
    """从用户饮品记录统计中推导画像候选值。

    基于用户历史饮品记录，分析出可能的偏好候选，
    如最常点的糖度、冰度、品牌等。

    参数:
        user_id: 用户标识符。

    返回:
        推导出的画像候选字典，可用于更新画像。
    """
    return repository.derive_profile_candidates_from_stats(user_id)


def refresh_profile_from_records(user_id: str) -> dict[str, Any]:
    """根据饮品记录刷新用户画像。

    从用户饮品记录中推导偏好候选，并应用到画像中。
    如果没有候选值，则返回当前画像不做更新。

    通常在以下场景调用:
    - 用户完成对话后（通过 memory job）
    - 用户主动请求刷新画像
    - 定期后台刷新任务

    参数:
        user_id: 用户标识符。

    返回:
        刷新后的用户画像。
    """
    # 从饮品记录统计中推导偏好候选
    candidates = derive_profile_candidates_from_stats(user_id)
    if not candidates:
        # 无候选值，返回当前画像
        return repository.get_profile(user_id)
    # 应用推导出的偏好候选
    return apply_profile_updates(user_id, candidates)
