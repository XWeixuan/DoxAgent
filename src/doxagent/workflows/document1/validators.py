"""Behavior-preserving mixin extracted from initialization.py."""
from doxagent.workflows.initialization.shared import *

class Document1ValidatorsMixin:

    def _ensure_global_research_section_content(self, checkpoint: WorkflowCheckpoint, section_key: str, section: ResearchSection, result: AgentResult) -> ResearchSection:
        tool_fragment = self._section_looks_like_tool_call_only(section)
        updates: dict[str, Any] = {}
        if tool_fragment or not self._has_chinese_text(section.text):
            updates['text'] = self._global_research_section_fallback_text(checkpoint, section_key, result)
        if tool_fragment or not self._has_chinese_text(section.summary):
            updates['summary'] = self._global_research_section_fallback_summary(checkpoint, section_key, result)
        if not updates:
            return section
        return section.model_copy(update=updates, deep=True)

    def _ensure_global_narrative_section_content(self, checkpoint: WorkflowCheckpoint, section: ResearchSection, result: AgentResult) -> ResearchSection:
        tool_call_only = self._section_looks_like_tool_call_only(section)
        updates: dict[str, Any] = {}
        if tool_call_only or not self._has_chinese_text(section.text):
            updates['text'] = self._global_narrative_fallback_text(checkpoint)
        if tool_call_only or not self._has_chinese_text(section.summary):
            updates['summary'] = self._global_narrative_fallback_summary(checkpoint)
        if not updates:
            return section
        return section.model_copy(update=updates, deep=True)

    def _section_looks_like_tool_call_only(self, section: ResearchSection) -> bool:
        text = f'{section.text}\n{section.summary}'.strip()
        if self._has_chinese_text(section.text) and self._has_chinese_text(section.summary):
            return False
        lowered = text.lower()
        markers = ('<tool_call', 'tool_call', 'name: doxa_get_narrative_report', '"name": "doxa_get_narrative_report"', 'doxa_get_narrative_report\narguments', 'arguments: ticker', 'symbol:', 'ticker:', 'outputsize:', 'interval:', 'query:', 'search_depth:', 'max_results:')
        marker_hits = sum((1 for marker in markers if marker in lowered))
        has_research_words = any((token in lowered for token in ('revenue', 'margin', 'capex', 'demand', 'valuation', 'cycle', 'macro', 'industry', 'price', 'risk', 'market')))
        return marker_hits >= 2 or (marker_hits >= 1 and (not has_research_words))

    def _global_research_section_fallback_text(self, checkpoint: WorkflowCheckpoint, section_key: str, result: AgentResult) -> str:
        source_summary = self._section_fallback_source_summary(result)
        ticker = checkpoint.ticker
        if section_key == 'fundamental_report':
            return f'{ticker} 的基本面段落在模型输出中未形成合格中文研究正文，workflow 已保留{source_summary}作为可追溯证据。当前可确认的研究方向是围绕收入增长、毛利率、资本开支、现金流、资产负债表和 SEC 披露继续核验 HBM 与 AI 存储需求对盈利质量的影响。该段不得被视为完整基本面结论；后续监控应优先补充最新财报拆分、管理层指引、自由现金流和同业估值证据。'
        if section_key == 'macro_report':
            return f'{ticker} 的宏观与市场环境段落在模型输出中退化为工具参数摘要，workflow 已用{source_summary}进行恢复。当前宏观层面的可用结论是：MU 的初始化假设需要同时观察美国科技股风险偏好、利率与美元环境、AI 基础设施资本开支节奏，以及半导体ETF/纳指基准的价格行为。现有证据足以支持把这些变量纳入后续监控，但不足以把单一宏观情景当作确定结论；若基准指数转弱或 hyperscaler capex 指引下修，应重新评估HBM 超级周期的估值支撑。'
        if section_key == 'industry_report':
            return f'{ticker} 的行业段落需要围绕存储周期、HBM 供需、DRAM/NAND 价格、竞争格局与同业估值展开。workflow 已保留{source_summary}作为证据底座；如果模型正文缺失，应把行业结论限制为可复核的供需与竞争假设，并把 WDC、STX、SK Hynix、Samsung 等同业数据缺口列为后续补证任务。'
        if section_key == 'market_trace_report':
            return f'{ticker} 的市场跟踪段落需要解释近期价格、成交量、相对 SOXX/QQQ 与存储同业的表现。workflow 已保留{source_summary}作为价格证据；如果模型正文缺失，当前只能把相对强弱、关键价量区间和波动率变化作为待复核信号，不能直接推出交易执行结论。'
        return f'{ticker} 的 {section_key} 段落未返回合格中文正文，workflow 已保留{source_summary}作为证据并标记为后续复核输入。'

    def _global_research_section_fallback_summary(self, checkpoint: WorkflowCheckpoint, section_key: str, result: AgentResult) -> str:
        source_summary = self._section_fallback_source_summary(result)
        labels = {'fundamental_report': '基本面', 'macro_report': '宏观与市场环境', 'industry_report': '行业与竞争格局', 'market_trace_report': '价格与资金行为'}
        label = labels.get(section_key, section_key)
        return f'{checkpoint.ticker} 的{label}段落已从不合格工具残片恢复为中文审计摘要；证据来自{source_summary}，结论需以后续补证和监控信号继续确认。'

    def _section_fallback_source_summary(self, result: AgentResult) -> str:
        names: list[str] = []
        for ref in []:
            metadata_tool = ref.retrieval_metadata.get('tool_name')
            label = metadata_tool or ref.source_id or ref.citation_scope
            if label:
                names.append(str(label))
        for call in result.tool_calls:
            if call.tool_name:
                names.append(call.tool_name)
        deduped = list(dict.fromkeys(names))
        if not deduped:
            return 'agent 输出'
        return '、'.join(deduped[:5])

    def _global_narrative_fallback_text(self, checkpoint: WorkflowCheckpoint) -> str:
        names = self._expectation_names_from_belief_state(checkpoint)
        focus = '、'.join(names[:3]) if names else checkpoint.ticker
        return f'基于已检索的 DoxAtlas 叙事报告与当前 Blackboard expectation units，{checkpoint.ticker} 的市场叙事应围绕 {focus} 展开。已定价部分主要来自已公开的业绩、供需、管理层指引与市场价格反应；尚未充分定价的部分需要继续观察后续订单、capex、毛利率、HBM 份额、客户认证或库存信号是否兑现。若证据只停留在工具检索摘要层，后续节点必须优先补强 DoxAtlas 原始事件、价格反应和反方不确定性引用。'

    def _global_narrative_fallback_summary(self, checkpoint: WorkflowCheckpoint) -> str:
        names = self._expectation_names_from_belief_state(checkpoint)
        focus = '、'.join(names[:2]) if names else checkpoint.ticker
        return f'市场叙事围绕 {focus} 的兑现程度、已定价证据与未定价监控信号继续跟踪。'
