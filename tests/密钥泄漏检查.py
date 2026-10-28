# -*- coding: utf-8 -*-
"""密钥泄漏检查 —— 防止 API Key 进入公开仓库

要求（用户明确提出）：
    API 密钥等敏感信息不得出现在公开仓库中，须通过环境变量方式配置

本检查做四件事：
  1. 确认 .env / .mcp.json 未被 git 跟踪
  2. 扫描【全部已被跟踪的文件】，确认不含 .env 里的任何真实密钥值
  3. 确认 .gitignore 仍包含必需的忽略规则（防止规则被误删）
  4. 扫描常见密钥特征串（sk-xxx / 32位与64位十六进制），
     兜住"密钥被硬编码进源码但没有同时写进 .env"的情况

用法：
    python tests/密钥泄漏检查.py
退出码非 0 表示存在泄漏风险。

建议在提交前运行；也可挂到 pre-commit 钩子上。
"""
import os
import re
import subprocess
import sys
import tempfile
from unittest.mock import patch

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJ)
# 用 app.py 相同的导入方式（项目根在 sys.path，走 water_quality_mcp.src.* 全路径）。
# 注意：环境里存在指向 D:\aiRAG 的 editable 安装，短路径 water_quality_mcp.X
# 会解析到那份代码而非本项目，故此处必须用全路径。
sys.path.insert(0, PROJ)
from scripts.secret_scan import is_known_text_hash, metadata_hash_spans

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding='utf-8', check=True).stdout


print("=" * 72)
print("密钥泄漏检查")
print("=" * 72)

tracked = [f for f in git("ls-files", "-z").split('\0') if f]
print(f"已跟踪文件: {len(tracked)} 个")

# ---------- 1. 敏感文件不得被跟踪 ----------
print()
print("1. 敏感文件是否被 git 跟踪")
SENSITIVE = [".env", ".mcp.json"]
tracked_set = set(tracked)
for f in SENSITIVE:
    check(f"{f} 未被跟踪", f not in tracked_set,
          "（文件存在但被忽略，符合预期）" if os.path.exists(f) else "（本就不存在）")

# ---------- 2. 扫描真实密钥值 ----------
print()
print("2. 已跟踪文件中是否含 .env 里的真实密钥值")
env_vals = {}
if os.path.isfile(".env"):
    for line in open(".env", encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            v = v.strip()
            # 跳过占位符与空值
            if v and not v.lower().startswith("your-") and len(v) >= 8:
                env_vals[k.strip()] = v

print(f"   .env 中待检密钥: {len(env_vals)} 个（只比对，不回显）")
leaks = []
for f in tracked:
    try:
        data = open(f, "rb").read()
    except OSError:
        continue
    for name, val in env_vals.items():
        if val.encode("utf-8") in data:
            leaks.append((f, name))

if leaks:
    for f, name in leaks:
        print(f"       ✗ {f}  含 {name}")
check("已跟踪文件中无 .env 的密钥值", not leaks, f"命中 {len(leaks)} 处")

# ---------- 3. .gitignore 规则仍在 ----------
print()
print("3. .gitignore 关键规则是否完好")
ignore_text = open(".gitignore", encoding="utf-8").read() if os.path.isfile(".gitignore") else ""
missing_rules = [r for r in SENSITIVE if not re.search(rf"^\s*{re.escape(r)}\s*$", ignore_text, re.M)]
check(".gitignore 仍忽略 .env 与 .mcp.json", not missing_rules,
      f"缺失: {missing_rules}" if missing_rules else "")

# ---------- 4. 特征串扫描 ----------
print()
print("4. 常见密钥特征串扫描（兜住未写入 .env 的硬编码）")
# DeepSeek / OpenAI 风格密钥
PAT_SK = re.compile(rb"sk-[A-Za-z0-9]{16,}")
# 32 位与 64 位十六进制（高德 Key 32 位；星图 Token 64 位）
PAT_HEX32 = re.compile(rb"\b[0-9a-f]{32}\b")
PAT_HEX64 = re.compile(rb"\b[0-9a-f]{64}\b")

# 允许出现的位置：示例文件、本检查自身、文档中的说明
ALLOWLIST = {".env.example", ".mcp.json.example", "tests/密钥泄漏检查.py"}

suspects = []
for f in tracked:
    if f in ALLOWLIST:
        continue
    if f.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".xlsx", ".db", ".sqlite3", ".pptx", ".docx")):
        continue
    try:
        data = open(f, "rb").read()
    except OSError:
        continue
    hash_spans = {64: metadata_hash_spans(f, data),
                  32: metadata_hash_spans(f, data, width=32)}
    for label, pat, width in (("sk-密钥", PAT_SK, 0), ("32位hex", PAT_HEX32, 32),
                              ("64位hex", PAT_HEX64, 64)):
        for m in pat.finditer(data):
            if width and m.span() in hash_spans[width]:
                continue  # 仅受限元数据字段；正文同值、非声明字段和真实.env值仍检查。
            hits = m.group(0).decode("ascii", "ignore")
            # 跳过明显的非密钥：全同字符、连续序列等；以及已核实的内容哈希。
            if len(set(hits)) <= 3 or is_known_text_hash(hits):
                continue
            suspects.append((f, label, hits[:8] + "..."))

if suspects:
    for f, label, masked in suspects:
        print(f"       ? {f}  疑似 {label}: {masked}")
check("未发现硬编码密钥特征串", not suspects, f"疑似 {len(suspects)} 处，请人工确认")

# ---------- 5. 加载优先级与冲突告警 ----------
print()
print("5. .env 与环境变量的优先级（曾出现'改了 .env 不生效'的静默失败）")
import logging as _logging
import io as _io

from water_quality_mcp.src.water_quality_mcp import config as _cfgmod
_orig = os.environ.get('DEEPSEEK_API_KEY')
_level = _cfgmod.logger.level
try:
    with tempfile.TemporaryDirectory(prefix='ci-env-priority-') as folder:
        _envpath = os.path.join(folder,'.env')
        _file_value = 'fixture-file-value-for-priority'
        with open(_envpath,'w',encoding='utf-8') as file:
            file.write('DEEPSEEK_API_KEY='+_file_value+'\n')
        with patch.object(_cfgmod,'DEFAULT_ENV_PATH',_envpath):
            _env_value = 'fixture-environment-value-for-priority'
            os.environ['DEEPSEEK_API_KEY'] = _env_value
            _buf = _io.StringIO()
            _h = _logging.StreamHandler(_buf)
            _cfgmod.logger.addHandler(_h)
            _cfgmod.logger.setLevel(_logging.WARNING)
            try:
                _cfgmod.load_env()
            finally:
                _cfgmod.logger.removeHandler(_h)
            check('.env 的值覆盖了环境变量', _cfgmod.resolve_api_key('DEEPSEEK_API_KEY') == _file_value,
                  '使用临时假配置；不读取或改写用户.env')
            check('冲突产生了告警', '不一致' in _buf.getvalue())
            check('告警中未回显完整密钥', all(v not in _buf.getvalue() for v in (_file_value,_env_value)))
            os.environ['DEEPSEEK_API_KEY'] = _env_value
            _cfgmod.load_env(override=False)
            check('override=False 时环境变量胜出', _cfgmod.resolve_api_key('DEEPSEEK_API_KEY') == _env_value)
finally:
    _cfgmod.logger.setLevel(_level)
    if _orig is None:
        os.environ.pop('DEEPSEEK_API_KEY',None)
    else:
        os.environ['DEEPSEEK_API_KEY'] = _orig

# ---------- 汇总 ----------
print()
print("=" * 72)
n_pass = sum(1 for _, ok in results if ok)
n_fail = len(results) - n_pass
print(f"结果: {n_pass} 通过, {n_fail} 失败, 共 {len(results)} 项")
if n_fail:
    print()
    print("存在泄漏风险。请把手动密钥移入 .env（该文件已被忽略），")
    print("并从源码/文档中删除明文，然后重新提交。")
print("=" * 72)
sys.exit(0 if n_fail == 0 else 1)
