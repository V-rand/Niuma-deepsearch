#!/usr/bin/env python3
"""
Filter quality test for the 3 filterable tools: case_retrieve, workspace_search, web_read.
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(override=True)

from agent_os.config import Settings
from agent_os.kernel.helpers import resolve_tool_content_for_messages
from agent_os.kernel.result_filter import ResultFilterAgent
from agent_os.tools.registry import ToolResult

s = Settings()
filter_ = ResultFilterAgent(api_key=s.api_key, base_url=s.base_url, model=s.model, timeout_seconds=120)
USER_MSG = "我在研究阿司匹林的作用机制和临床应用。"

def sz(data) -> str:
    s = len(json.dumps(data, ensure_ascii=False, default=str))
    return f"{s/1000:.0f}K" if s > 1000 else f"{s}B"

async def test_one(name: str, result: ToolResult):
    print(f"\n{'='*60}\nTOOL: {name}\n{'='*60}", flush=True)
    if not result.success:
        print(f"  ERROR: {result.error}", flush=True); return

    filtered = await resolve_tool_content_for_messages(
        tool_name=name, result=result, user_message=USER_MSG,
        result_filter=filter_, filterable=True)
    unfiltered = await resolve_tool_content_for_messages(
        tool_name=name, result=result, user_message=USER_MSG,
        result_filter=filter_, filterable=False)

    fd = json.loads(filtered)
    has_filter = fd.get("data", {}).get("_filtered", False)

    print(f"  Raw:   {sz(result.data)}", flush=True)
    print(f"  Without filter: {sz(unfiltered)}", flush=True)
    print(f"  With filter:    {sz(filtered)}", end="", flush=True)
    print(" ✅" if has_filter else " ⏭️  (skipped)", flush=True)

    if has_filter:
        summary = fd["data"].get("filtered_summary", "")
        if isinstance(summary, str):
            print(f"  Summary: {len(summary)} chars", flush=True)
            print(f"  {'─'*50}", flush=True)
            for line in summary.split("\n")[:35]:
                print(f"  {line}", flush=True)
            print(f"  {'─'*50}", flush=True)

async def main():
    # 1) web_read — 真实大页面
    from agent_os.tools.web import handle_web_read
    print("\n[1/3] web_read: 维基百科阿司匹林...", flush=True)
    r = await handle_web_read(url="https://zh.wikipedia.org/wiki/%E9%98%BF%E5%8F%B8%E5%8C%B9%E6%9E%97")
    await test_one("web_read", r)

    # 2) workspace_search — 模拟多文件内容
    print("\n[2/3] workspace_search: 多文件检索...", flush=True)
    docs = [
        {"title": "药理笔记", "content": "阿司匹林通过不可逆抑制COX-1和COX-2，阻断前列腺素合成。低剂量（75-100mg）主要抑制COX-1，用于抗血小板；高剂量（300-8000mg）抑制COX-2，用于解热镇痛抗炎。口服后30分钟起效，半衰期15-20分钟。代谢产物水杨酸具有更长的半衰期2-3小时。" * 50, "path": "/workspace/pharmacology.md"},
        {"title": "临床指南", "content": "阿司匹林用于心血管疾病一级预防推荐剂量75-100mg/日。二级预防所有患者均应长期服用。主要副作用为胃肠道出血，可联用PPI保护。中国指南不推荐70岁以上老年人常规服用阿司匹林进行一级预防。" * 50, "path": "/workspace/guideline.md"},
        {"title": "不良反应研究", "content": "阿司匹林引发出血的机制：抑制血小板COX-1减少TXA2生成，同时抑制胃黏膜COX-1减少前列腺素E2合成，导致胃黏膜保护作用减弱。出血风险与剂量正相关。联用抗凝药时风险显著增高。" * 50, "path": "/workspace/adverse_effects.md"},
    ]
    r = ToolResult.ok(data={"query": "阿司匹林 作用机制", "results": docs, "count": len(docs)})
    await test_one("workspace_search", r)

    # 3) case_retrieve — 模拟多案例（真实裁判文书通常每篇数千字）
    print("\n[3/3] case_retrieve: 知识产权案例...", flush=True)
    cases = [
        {"title": "A公司诉B公司专利侵权案", "content": "原告A公司拥有发明专利'一种缓释阿司匹林制剂'（专利号ZL201810123456.7），被告B公司生产的'阿司匹林肠溶片'落入其权利要求保护范围。原告主张被告未经许可为生产经营目的制造使用许诺销售销售其专利产品构成侵权。法院委托鉴定机构进行技术比对鉴定结论为等同侵权。被告辩称其产品采用不同辅料和工艺不构成侵权。法院认为虽然被告的辅料组成与原告专利不同但实现的技术功能和效果实质相同属于等同技术特征。依据《专利法》第65条判决B公司停止侵权赔偿损失300万元。二审维持原判。" * 8, "court": "北京知识产权法院", "case_id": "(2023)京73民初123号"},
        {"title": "C公司诉D公司商标侵权及不正当竞争案", "content": "原告C公司是'BAYER'注册商标权利人被告D公司在同类药品上使用近似标识'BAYER PLUS'构成商标侵权。原告另主张被告的包装装潢与其知名商品'阿司匹林片'近似构成不正当竞争。法院审理认为'BAYER'商标在药品领域具有较高知名度被告的使用容易导致相关公众混淆构成商标侵权。但关于包装装潢法院认为两种设计的整体视觉效果存在明显差异不构成近似故不支持不正当竞争主张。综合考虑被告的侵权故意和持续时间酌定赔偿50万元并判令停止使用侵权标识。一审判决后双方均未上诉。" * 6, "court": "上海知识产权法院", "case_id": "(2022)沪73民终456号"},
        {"title": "E某诉F公司职务发明创造发明人报酬案", "content": "原告E某主张其在F公司工作期间开发的阿司匹林新剂型配方属于其个人发明F公司主张该配方属职务发明。E某在F公司担任制剂研究员其岗位职责包括新剂型的开发。涉案配方系E某利用公司实验设备和原材料完成且与其岗位职责直接相关。法院认定该配方为职务发明创造专利权归属于F公司。但法院同时指出E某作为发明人有权获得合理报酬。F公司获得专利权后未实际实施该专利也未与E某协商报酬。参照《专利法实施细则》第78条酌定F公司向E某支付报酬20万元。" * 7, "court": "最高人民法院知识产权法庭", "case_id": "(2023)最高法知民终789号"},
    ]
    r = ToolResult.ok(data={"query": "阿司匹林 专利 案例", "results": cases, "count": len(cases)})
    await test_one("case_retrieve", r)

    print(f"\n{'='*60}\nDONE", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
