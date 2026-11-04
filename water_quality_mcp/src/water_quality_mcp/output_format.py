"""Bounded, local-only answer-formatting skill shared by model output paths.

Only the reviewed prompt block is loaded; upstream scripts and workflow text
are provenance, never executed or appended to model messages.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
SKILL_PATH = Path(__file__).parent / 'skills' / 'baoyu-format-markdown' / 'SKILL.md'
PROMPT_START = '<!-- model-prompt:start -->'
PROMPT_END = '<!-- model-prompt:end -->'
MAX_PROMPT_CHARS = 4000


def load_format_skill(path: Path = SKILL_PATH) -> str:
    """Read one trusted bundled skill; malformed/missing files keep prior behavior."""
    try:
        text = path.read_text(encoding='utf-8')
        if text.count(PROMPT_START) != 1 or text.count(PROMPT_END) != 1:
            raise ValueError('format skill markers are missing or duplicated')
        start = text.index(PROMPT_START) + len(PROMPT_START)
        end = text.index(PROMPT_END)
        prompt = text[start:end].strip() if end > start else ''
        if not prompt or len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError('format skill prompt is empty or exceeds its size limit')
        return '\n\n' + prompt
    except (OSError, UnicodeError, ValueError) as exc:
        logger.warning('回答排版 Skill 未加载，保留原有表达规范：%s', type(exc).__name__)
        return ''


MARKDOWN_FORMAT_SKILL = load_format_skill()
