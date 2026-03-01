"""
Table Extraction Module
Logic: Reconstructs 2D table topology from PDF streams.
"""
import pandas as pd
from typing import List, Optional, Dict

class TableArtifact:
    def __init__(self, page_num: int, markdown: str, raw_data: List[List[str]]):
        self.page_num = page_num
        self.markdown = markdown
        self.raw_data = raw_data
        # 增加语义指纹，帮助检索
        self.header_fingerprint = " ".join([str(x) for x in raw_data[0]]) if raw_data else ""

def extract_tables_from_page(page_obj) -> List[TableArtifact]:
    """
    Extracts tables and converts them to LLM-friendly Markdown.
    """
    artifacts = []
    
    # 1. 使用 pdfplumber 的表格提取算法
    # vertical_strategy="text" 适用于无边框表格（常见于数据手册）
    # intersection_tolerance 容忍线条没完全闭合的情况
    try:
        tables = page_obj.extract_tables(
            table_settings={
                "vertical_strategy": "text", 
                "horizontal_strategy": "text",
                "intersection_y_tolerance": 5
            }
        )
    except Exception:
        # Fallback if extraction fails
        return []
    
    for table_data in tables:
        # 清洗数据：去除 None 和换行符
        clean_data = [
            [str(cell).replace('\n', ' ').strip() if cell else "" for cell in row]
            for row in table_data
        ]
        
        # 过滤无效小表格（噪音）
        if len(clean_data) < 2 or len(clean_data[0]) < 2:
            continue
            
        # 2. 转为 Pandas DataFrame 再转 Markdown
        try:
            # Assume first row is header
            headers = clean_data[0]
            rows = clean_data[1:]
            
            # Handle duplicate columns if any to avoid pandas errors
            # (Simple dedup strategy)
            seen_headers = {}
            dedup_headers = []
            for h in headers:
                if h in seen_headers:
                    seen_headers[h] += 1
                    dedup_headers.append(f"{h}_{seen_headers[h]}")
                else:
                    seen_headers[h] = 0
                    dedup_headers.append(h)
            
            df = pd.DataFrame(rows, columns=dedup_headers)
            markdown = df.to_markdown(index=False)
            
            artifacts.append(TableArtifact(
                page_num=page_obj.page_number,
                markdown=markdown,
                raw_data=clean_data
            ))
        except Exception:
            # 容错：如果表头对齐失败，放弃结构化，回退到文本
            continue
            
    return artifacts
