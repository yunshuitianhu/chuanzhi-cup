# RiverView 迟到响应修复（2026-10-29）

> 基线：`dcebb99`
> 提交：`8e7278a`（迟到失败）、`5a10914`（迟到参数建议）、`f75f2d7`（回归）
> 范围：`frontend/src/views/RiverView.vue` 与其请求状态测试；不改后端接口。

## 1. 参数编辑后的旧请求失败

**问题**：`runInit/runHydro/runPollution` 的成功分支会检查 `request.matches()`，但 `catch` 只检查 `request.current()`。用户在请求期间修改参数后，旧请求的失败仍会把步骤标成“失败”，并覆盖“参数已变化，请重新…”的提示。

**修复**：三个 `catch` 统一改为 `if (request.matches())` 才写入 `failedStep` 与 `runNotice`。参数已变化或被新请求取代时，只保留 watcher 产生的参数变化提示；旧请求不再污染当前步骤状态。

**回归**：三个动作各一项“参数编辑后延迟失败不标记当前步骤”；另有一项“未编辑请求的失败仍标记当前步骤”，防止把所有失败都吞掉。

## 2. 项目切换后的迟到自动参数建议

**问题**：`detectParams` 只比较 `analysis_revision`。项目切换时该版本可能不变，迟到的 `riverParams` 响应会把旧项目的河道名称、长度、流量写进新项目表单；迟到失败也会把旧项目的错误显示在新项目上。

**修复**：
- 每次检测使用递增 `paramsRequest` ticket，并同时绑定 `analysis_revision`、`workspaceRevision`、当前项目 ID、`disposed` 与 `switchingProject`。
- 成功/失败只有在全部上下文仍一致时才写入 `params`/`paramsNotice`/表单。
- `finally` 只清理仍属于当前 ticket 且仍为 `params` 的 busy，避免旧请求清掉新请求的“检测中”，也避免项目切换后本页卡在检测中。

**回归**：
- 同一项目的建议正常应用；
- 项目/工作区切换后迟到的成功不写表单；
- 项目/工作区切换后迟到的失败不写提示；
- `busy` 在以上路径均能退出。

## 验证

- `node --test frontend/tests/riverRequestState.test.mjs`：**15 passed**。
- `npm --prefix frontend run check`：**373 tests passed**，TypeScript/SFC 与生产构建通过。
- 未运行浏览器项目切换走查；本修复只覆盖组件请求状态与受控 stub，不宣称真实用户或跨设备验收。

## 残余

- `detectParams` 仍不会主动取消已发出的 HTTP 请求；本次只是在响应返回时丢弃过期结果。
- 真实项目切换的页面级走查仍建议在后续 Q17/N07 交互验收中补做。
