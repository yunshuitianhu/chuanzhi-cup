"""把模型服务异常转成用户能操作的中文提示，不回显请求、Key 或完整响应。"""

from __future__ import annotations


def model_error_message(exc: BaseException) -> str:
    text = str(exc or '')
    lower = text.lower()
    if any(token in lower for token in ('api key', 'unauthorized', 'authentication', '401')):
        return 'DeepSeek 认证失败：请核对服务端 DEEPSEEK_API_KEY、账户状态和模型权限；基础示例、图表和筛查不受影响。'
    if any(token in lower for token in ('quota', 'insufficient', 'balance', '额度', '余额', '429')):
        return 'DeepSeek 请求额度或频率受限：请核对账户余额，稍后重试；数据和草稿仍保留。'
    if any(token in lower for token in ('forbidden', 'permission', '403')):
        return 'DeepSeek 访问权限不足：请核对 Key 的模型/接口权限；基础示例、图表和筛查仍可用。'
    if any(token in lower for token in ('timeout', 'timed out', 'connect', 'network', '网络', '超时')):
        return '无法连接 DeepSeek 或请求超时：请检查网络/代理后重试；问题、附件和草稿已保留。'
    if any(token in lower for token in ('500', '502', '503', 'server error', '服务端')):
        return 'DeepSeek 服务端暂时不可用：请稍后重试；基础示例、图表和筛查不受影响。'
    # Agent 自身抛出的校验/安全错误已是面向用户的中文说明（引用未通过核对、
    # 密钥已脱敏等），保留原文比统一改写成网络失败更准确。传输层异常通常为
    # 英文，已在上面分类。
    if any('一' <= char <= '鿿' for char in text):
        return text[:300]
    return 'AI 请求未完成：请检查服务端配置、网络和服务状态后重试；问题与草稿已保留。'
