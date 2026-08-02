import os
import json
import time
import random
import re
import requests
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

# ================= 配置区域：人物监控矩阵 =================
# name: 姓名 
# base_keyword: 交给搜索引擎的词（姓名+地域锚点，避免职位变动搜不到）
# tags: 用于本地二次验证的特征词库（包含现职务、可能的晋升职务及动作词）
OFFICIALS = [
    {"name": "胡继军", "base_keyword": "胡继军 沈阳", "tags": ["和平区", "教委", "书记", "教育", "任免", "公示", "调研", "区委", "视察"]},
    {"name": "王丽坤", "base_keyword": "王丽坤 辽宁", "tags": ["广电局", "书记", "广播", "电视", "任免", "公示", "调研", "厅长", "视察"]},
    {"name": "赵正武", "base_keyword": "赵正武 沈阳", "tags": ["水务局", "水利", "处长", "任免", "公示", "调研", "局长", "视察"]},
    {"name": "范骁锋", "base_keyword": "范骁锋 辽宁", "tags": ["水利厅", "副厅长", "厅长", "任免", "公示", "调研", "防汛", "视察"]},
    {"name": "张春达", "base_keyword": "张春达 长春", "tags": ["副市长", "市长", "政府", "任免", "公示", "调研", "书记", "视察"]},
    {"name": "杜鑫", "base_keyword": "杜鑫 辽宁", "tags": ["农科院", "农业", "书记", "院长", "任免", "公示", "调研", "视察"]},
    {"name": "王长军", "base_keyword": "王长军 抚顺", "tags": ["望花区", "人大", "主席", "任免", "公示", "调研", "书记", "视察"]},
    {"name": "纪政", "base_keyword": "纪政 沈阳", "tags": ["政协", "副主席", "主席", "任免", "公示", "调研", "视察"]},
    {"name": "陈万松", "base_keyword": "陈万松 辽宁", "tags": ["司法厅", "政治部", "主任", "任免", "公示", "调研", "厅长", "视察"]},
    {"name": "张君昶", "base_keyword": "张君昶 抚顺", "tags": ["副市长", "市长", "政府", "任免", "公示", "调研", "书记", "视察"]},
    {"name": "刘宇星", "base_keyword": "刘宇星 抚顺", "tags": ["社会工作部", "市委", "部长", "任免", "公示", "调研", "视察"]},
    {"name": "王昕", "base_keyword": "王昕 辽阳", "tags": ["政府", "副秘书长", "秘书长", "任免", "公示", "调研", "市长", "视察"]},
    {"name": "白勒", "base_keyword": "白勒 辽阳", "tags": ["交通运输局", "办公室", "主任", "任免", "公示", "调研", "局长", "视察"]},
    {"name": "申建军", "base_keyword": "申建军 国研中心", "tags": ["国务院发展研究中心", "任免", "公示", "调研", "主任", "视察"]},
    {"name": "李军", "base_keyword": "李军 总政治部", "tags": ["总政", "军队", "军委", "任免", "公示", "视察", "主任", "少将", "中将"]}
]

# 抓取渠道：针对人物新闻，重点使用大型新闻聚合搜索引擎
SCRAPE_SOURCES = {
    "百度资讯": "https://www.baidu.com/s?tn=news&word={keyword}",
    "头条搜索": "https://so.toutiao.com/search?dvpf=pc&source=input&keyword={keyword}",
    "澎湃新闻": "https://www.thepaper.cn/searchResult?cont={keyword}"
}

HISTORY_FILE = "history.json"
WECHAT_WEBHOOK = os.getenv("WECHAT_WEBHOOK_URL")
TZ_BJ = timezone(timedelta(hours=8))

# ================= 辅助函数 =================

def is_published_today(text, today_str):
    """
    判断文本中包含的日期是否是今天。
    兼容具体日期 (2024-05-12) 以及新闻网站常用的相对时间 (如 "2小时前", "刚刚")
    """
    if not text:
        return False, "未知"
        
    # 1. 检查具体的年月日正则匹配
    match = re.search(r'(20\d{2})[-/年\.](\d{1,2})[-/月\.](\d{1,2})', text)
    if match:
        year, month, day = match.groups()
        date_str = f"{year}-{int(month):02d}-{int(day):02d}"
        if date_str == today_str:
            return True, date_str
            
    # 2. 检查新闻高频的相对时间词
    relative_time_keywords = ["分钟前", "小时前", "刚刚", "今天"]
    for kw in relative_time_keywords:
        if kw in text:
            # 提取具体的相对时间短语用于展示
            context_match = re.search(fr'(\d+)?{kw}', text)
            show_date = context_match.group() if context_match else kw
            return True, f"今天 ({show_date})"
            
    return False, "非今日"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_history(history_list):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history_list, f, ensure_ascii=False, indent=2)

def send_wechat_msg(title, person_name, source, url, publish_date, current_time):
    if not WECHAT_WEBHOOK:
        return

    # 微信 Markdown 模板特化
    markdown_content = f"""<font color="warning">📰 政务信息今日最新进展. </font>
> **政务对象**: <font color="info">{person_name}</font>
> **新闻标题**: **{title}**
> **信息渠道**: {source}
> **发布时间**: <font color="comment">{publish_date}</font>
> **推送时间**: {current_time}

[🔗 点击跳转查看原文]({url})"""

    payload = {
        "msgtype": "markdown",
        "markdown": {"content": markdown_content}
    }
    
    try:
        requests.post(WECHAT_WEBHOOK, json=payload, timeout=10)
    except Exception:
        pass

# ================= 核心逻辑 =================

def run_scraper():
    history = load_history()
    new_findings_count = 0
    
    today_str = datetime.now(TZ_BJ).strftime("%Y-%m-%d")
    current_time_str = datetime.now(TZ_BJ).strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"--- 启动人物监控扫描，目标匹配日期: {today_str} ---")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        page.set_default_timeout(25000) 

        for official in OFFICIALS:
            person_name = official["name"]
            base_keyword = official["base_keyword"]
            
            for source_name, source_url_template in SCRAPE_SOURCES.items():
                target_url = source_url_template.format(keyword=base_keyword)
                print(f"扫描: {source_name} - {person_name}")
                
                try:
                    page.goto(target_url)
                    page.wait_for_timeout(random.randint(2000, 4000)) 
                    
                    links = page.locator("a").element_handles()
                    
                    for link in links:
                        try:
                            title = link.inner_text().strip()
                            url = link.get_attribute("href")
                            
                            if url and url.startswith("http") and len(title) > 4:
                                
                                # 核心规则1：新闻标题中必须含有该官员的姓名
                                if person_name in title:
                                    
                                    # 提取上下文(包含摘要、时间标签)
                                    try:
                                        context_text = link.evaluate(
                                            "node => { "
                                            "  let p = node.parentElement; "
                                            "  let gp = p ? p.parentElement : null; "
                                            "  let ggp = gp ? gp.parentElement : null; "
                                            "  return node.textContent + ' ' + (p ? p.textContent : '') + ' ' + (gp ? gp.textContent : '') + ' ' + (ggp ? ggp.textContent : ''); "
                                            "}"
                                        )
                                    except:
                                        context_text = title

                                    # 核心规则2：判断时间是否是今天
                                    is_today, display_date = is_published_today(context_text, today_str)
                                    
                                    if is_today:
                                        
                                        # 核心规则3：防同名同姓误报验证。
                                        # (姓名和地域已经通过搜索框卡死了一部分，这里确保上下文带有相关特征词)
                                        # 如果是超短名字，放宽限制；如果是特定动作词汇，直接放行。
                                        context_clean = context_text.replace(" ", "").replace("\n", "")
                                        match_tag = any(tag in context_clean for tag in official["tags"])
                                        
                                        # "任免"、"公示" 这类极度敏感的词如果出现，直接判定有效
                                        critical_keywords = ["任免", "公示", "履新", "去职", "调任", "提名"]
                                        has_critical = any(kw in context_clean for kw in critical_keywords)

                                        if match_tag or has_critical:
                                            data_hash = f"{person_name}_{source_name}_{url}"
                                            
                                            if data_hash not in history:
                                                print(f"发现今日新动态: [{person_name}] - {title}")
                                                
                                                send_wechat_msg(
                                                    title=title, 
                                                    person_name=person_name, 
                                                    source=source_name, 
                                                    url=url, 
                                                    publish_date=display_date,
                                                    current_time=current_time_str
                                                )
                                                
                                                history.append(data_hash)
                                                new_findings_count += 1
                        except Exception:
                            continue

                except Exception as outer_e:
                    continue
                    
        browser.close()
        
    if new_findings_count > 0:
        save_history(history)
        print(f"本次运行完成，共推送 {new_findings_count} 条官员今日动态。")
    else:
        print("本次运行完成，未发现今日新动态。")

if __name__ == "__main__":
    run_scraper()
