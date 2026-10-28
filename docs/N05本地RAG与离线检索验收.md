# N05 本地 RAG 与离线检索验收

> 日期：2026-10-28
> 对应任务：`docs/下一阶段详细任务_N04-N05与交互验收_20261028.md` 的 N05-C。
> 结论：**本地模型 + 匹配索引的真实离线检索已跑通（RG05）；缺少模型缓存、索引缺失/损坏也有实际结果。
> 但 LLM 综合回答、无关资料拒答和超时/停止未完成，不能写“完整离线 RAG 可用”。**

## 1. 条件与隔离

| 项 | 实际值 |
| --- | --- |
| 运行环境 | Windows 11、Python 3.14.0、共享 venv（chromadb 1.5.8、sentence-transformers 5.5.1、torch 2.10.0+cu128） |
| 嵌入模型 | `shibing624/text2vec-base-chinese`，本地缓存 revision `183bb99aa7af74355fb58d16edf8c13ae7c5433e` |
| 索引 | 复制项目 `.rag_index` 到 `work/rag_probe_20261028/index`，1 个 sqlite 文件，1,810,432 字节 |
| 原索引 | 未修改；检索只读隔离副本 |
| 离线约束 | `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、`HF_DATASETS_OFFLINE=1` |
| 网络隔离范围 | 禁止 Hugging Face 下载；**不是防火墙级断网**，不能证明进程零网络包 |
| 与 LLM 的关系 | 本轮未配置/调用 LLM；本地检索和综合回答分开判断 |

复现命令（路径和解释器换成接收方实际值；不要指向用户原索引）：

```powershell
$env:WQ_RAG_INDEX_DIR = 'D:\隔离目录\rag-index'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
& 'D:\隔离目录\.venv\Scripts\python.exe' 'scripts\evaluate_rag.py' --index-dir $env:WQ_RAG_INDEX_DIR
```

本次没有重建索引；只复制并检索。需要重建时使用新目录，不能覆盖用户原 `.rag_index`。

## 2. 真实检索结果（RG05、RG08）

查询：`黑臭水体治理`，`top_k=3`，实际耗时 8.192 秒。

| 排名 | 标题 | 来源文件 | 相似度 | 证据 ID |
| --- | --- | --- | --- | --- |
| 1 | 苏州河道黑臭水体治理案例 | `黑臭水体/苏州河道黑臭水体治理案例.md` | 0.6456 | `...md_2`、`...md_0` |
| 2 | 江苏常州龙城河人工湿地黑臭水体治理案例 | `黑臭水体/江苏常州龙城河人工湿地黑臭水体治理案例.md` | 0.6258 | `...md_0`、`...md_4` |
| 3 | 东莞高埗凌屋村一支渠农村黑臭水体治理案例 | `黑臭水体/东莞高埗凌屋村一支渠农村黑臭水体治理案例.md` | 0.6038 | `...md_3`、`...md_0` |

这证明：匹配模型、索引和依赖存在时，检索在 HF 离线约束下能返回真实来源和证据 ID，不需要 LLM Key。
相似度只是排序分数，不是治理效果、现场准确率或责任概率；资料原始发布内容仍未独立核验。

## 3. 缺少模型缓存（RG06）

把 `HF_HOME` 和 `SENTENCE_TRANSFORMERS_HOME` 指向空目录，保持 HF 离线，再查同一问题：

```json
{
  "status": "unavailable",
  "elapsed_s": 7.81,
  "response": {
    "状态": "知识库查询失败（OSError），检索未完成",
    "建议": "检查索引与依赖版本兼容性；当前结果不包含治理案例。"
  }
}
```

实际结果是 `LocalEntryNotFoundError`（缓存无模型且外发已禁用），7.81 秒内失败，没有下载模型、没有返回伪造案例。
日志中有完整 traceback，但 API 只返回类型名；不包含本机绝对路径。

## 4. 索引与依赖状态（RG01—RG04）

| ID | 条件 | 实际结果 |
| --- | --- | --- |
| RG01 | 干净 CI venv，缺 ChromaDB/嵌入依赖 | `/api/rag/status`：`state=dependency_missing`、`stage=dependencies`、`available=false`；基础服务仍可用 |
| RG02 | 模型本地路径不存在 + 离线 | 检索 7.064 秒返回 `FileNotFoundError`、状态“检索未完成”、无案例；错误文案没有单独指出模型路径，属部分完成 |
| RG03 | 空索引目录 | `/api/rag/status`：`state=index_missing`、`stage=index_build`、`has_index=false` |
| RG04 | 损坏的 `chroma.sqlite3` | `/api/rag/status`：`state=index_unreadable`、`stage=index_read`、`InternalError`；提示核对目录权限与依赖版本 |

RG01/RG03/RG04 的状态接口不返回 `index_dir`；基础示例、筛查和手工模型不受 RAG 失败影响。

## 5. 无关查询与回答层（RG07、RG09、RG10）

查询 `量子计算机芯片制造工艺`，真实检索仍返回 3 个最近邻案例，相似度分别为
0.4593、0.4427、0.4133，标题均为氨氮/渗滤液治理，与问题无关。

- 当前检索没有相似度下限；它会返回最近邻，是否“未取得相关资料”由后续回答层判断。
- RAG 规则已要求“资料未提供所问事实、时段或对象时须说明缺失”，但本轮没有 LLM，**回答层是否拒答未验证**。
- 因此 RG09 只能标为部分完成；不能因为返回了 3 条低相似度案例就说资料匹配成功。
- RG07（检索成功但 LLM 不可用）和 RG10（下载/加载/检索超时或停止）本轮未运行。

是否增加相似度下限需要先在不降低固定集召回的前提下评测；本轮不凭一次无关查询改检索算法。

## 6. 逐项状态

| ID | 状态 | 说明 |
| --- | --- | --- |
| RG01 | 通过（受控） | 干净 venv 缺依赖，状态分阶段 |
| RG02 | 部分完成 | 快速失败、无案例；模型路径文案不够具体 |
| RG03 | 通过（实际） | 空索引 → `index_missing` |
| RG04 | 通过（实际） | 损坏索引 → `index_unreadable` |
| RG05 | 通过（实际） | 匹配模型/索引 + 离线，真实检索 3 案例 |
| RG06 | 通过（实际） | 空缓存 + 离线，OSError，无下载/无案例 |
| RG07 | 未运行 | 无 LLM 配置 |
| RG08 | 部分完成 | 真实来源/证据 ID；未由 LLM 引用与核对正文 |
| RG09 | 部分完成 | 无关查询返回低相似度最近邻；回答层拒答未验证 |
| RG10 | 未运行 | 未注入超时/停止 |

## 7. 限制

- 使用共享环境，不是干净安装；`requirements-lock.txt` 的完整依赖组合未在干净 venv 验证。
- 模型缓存已存在；“首次下载”场景未执行，下载体积和耗时不能从本轮推断。
- 离线由 HF 环境变量约束，不是防火墙/断网环境；不能宣称零网络包。
- 未调用 LLM，因此不能把 RG05 的检索成功写成“RAG 综合回答成功”。
- 未修改用户原 `.rag_index`；没有重建索引，也没有改变相似度阈值。
- 相似度和引用 ID 不等于现场准确率或污染责任。

机器可读记录：`docs/validation/rag_offline_20261028.json`。
