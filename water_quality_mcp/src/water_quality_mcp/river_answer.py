"""河道概况回答约定；要求说清情况，也允许明确说明未形成判断。"""
import re

OVERVIEW_HEADINGS = ('河道污染情况','主要污染类型','最可能的污染源头','建议治理方案')
OVERVIEW_PROMPT = '''
本轮必须给用户一份直接、通俗、可行动的河道情况说明。先用两句话说清主要问题及优先行动，随后依次使用以下四个二级标题，每节必须有实际内容：
## 河道污染情况
直接说当前记录反映是否异常、问题集中在哪些采样位置/时间及严重程度；用少量本次工具数值、单位、规则和引用支持，不以工具执行清单代替判断。零预警要直说，不等于排除所有风险；质控异常需区分仪器线索。必须先获取当前数据摘要/筛查证据，不沿用过期结果。
## 主要污染类型
先点名有证据支持的异常指标及表现，再解释可能对应的污染特征。不能从氨氮单项直接确定生活污水、工业排放等成因；证据不足时直接说明“目前能判断的是……，尚不能确定的是……”。不能把缺测、插值或图像颜色当化验结果，不混用COD与CBOD。
## 最可能的污染源头
有可区分的证据时给出优先核查的源头类别/入河方向、为什么优先、相反证据及确认办法；证据不足时明确说“现有证据还不能把某一类源头排到最前”，随后给用户可执行的优先排查方向和缺少的证据。不得编造概率、企业责任或用热点数代替源数；仿真样例不得指认真实企业。假定排放源不是识别出的源头。
## 建议治理方案
围绕实际异常给出有条件的控源排查、现场处置/生态修复候选和复测验证顺序，说明适用条件。已有案例须按真实检索材料引用；无治理案例时可给一般性待核实行动，不能伪造已有方案、成本、药剂剂量或保证效果。资料少也应说清现在能做什么、补什么资料再决定具体工程。
四项不能省略，也不能用免责声明充当答案；没有监测数据时四项都明确标为尚无法判断，并告知导入什么资料。真实数据、规则派生与假定情景须区分。普通用户少用缩写、术语和大表格，必要术语一句话解释。此约定不降低既有证据、引文、安全和仿真身份要求。
'''


def requires_overview(text, *, auto=False, has_data=False, first_answer=False, selected_context=False):
    if auto:
        return True
    question = str(text or '')
    if re.search(r'什么是|概念|定义',question) and not re.search(r'当前|本次|这条|这段|这批',question):
        return False
    river = bool(re.search(r'(河道|河水|水体|这条河|这段河).{0,18}(情况|现状|污染|水质|怎么样|如何|治理|源头|问题|正常|干净|黑臭|怎么|咋样)|(?:污染|水质).{0,12}(情况|现状|类型|源头|治理方案)',question))
    initial = has_data and first_answer and not selected_context and any(w in question for w in ('分析','研判','检测','评估','看看','解读','这批数据','这批读数'))
    return river or initial


def missing_overview_sections(text):
    # Require a heading and a nonempty body; a bare checklist does not count.
    parts = re.split(r'^\s*#{2,3}\s+',text,flags=re.M)
    present = set()
    for part in parts[1:]:
        title,_,body = part.partition('\n')
        title = re.sub(r'^(?:第?[一二三四1-4]+[、.．)）\s]+)', '', title.strip().strip('*'))
        if title in OVERVIEW_HEADINGS and body.strip():
            present.add(title)
    return [heading for heading in OVERVIEW_HEADINGS if heading not in present]


def missing_sections_notice(missing):
    return '\n\n> 本轮说明尚未完整覆盖以下问题，需继续补充分析；不能把未给出的判断当成已有结论。\n\n' + '\n\n'.join(
        f'## {heading}\n\n本轮尚未形成这一项的完整回答。请要求补充当前证据、可作出的判断及下一步行动。' for heading in missing)
