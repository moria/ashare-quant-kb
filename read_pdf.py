#!/usr/bin/env python3
"""
read_pdf.py — DeepResearch PDF 阅读工具

用 PyMuPDF 提取 PDF 中的文字和表格，支持按页提取、关键词搜索、图片导出。

用法:
    # 提取全文
    python3 read_pdf.py --file "目录.pdf"

    # 提取指定页（从0开始）
    python3 read_pdf.py --file "手册.pdf" --pages 3-8

    # 搜索关键词，只输出包含关键词的页
    python3 read_pdf.py --file "手册.pdf" --search "cabinet dimensions"

    # 多关键词 OR 搜索（匹配任一关键词的页）
    python3 read_pdf.py --file "手册.pdf" --search-any "floor unit,width,drawer"

    # 摘要模式：每页只输出匹配关键词周围的上下文片段
    python3 read_pdf.py --file "手册.pdf" --search-any "corner unit,dimensions" --summary

    # 限制最多返回页数
    python3 read_pdf.py --file "手册.pdf" --search-any "hinge,drawer" --summary --max-results 10

    # 提取所有图片到指定目录
    python3 read_pdf.py --file "手册.pdf" --extract-images "images/"

    # 输出为 markdown 格式
    python3 read_pdf.py --file "手册.pdf" --pages 5-10 --markdown

    # 提取表格
    python3 read_pdf.py --file "手册.pdf" --pages 5-10 --tables
"""

import argparse
import sys
import os
import re

try:
    import fitz  # PyMuPDF
except ImportError:
    print("错误: 未安装 PyMuPDF。运行: pip install PyMuPDF", file=sys.stderr)
    sys.exit(1)


def parse_page_range(page_str: str, total_pages: int) -> list[int]:
    """解析页码范围，如 '3-8' 或 '1,3,5-10'"""
    pages = []
    for part in page_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start = int(start)
            end = min(int(end), total_pages - 1)
            pages.extend(range(start, end + 1))
        else:
            p = int(part)
            if p < total_pages:
                pages.append(p)
    return sorted(set(pages))


def _matches_any_keyword(text_lower: str, keywords: list[str]) -> list[str]:
    """返回在 text 中匹配到的关键词列表"""
    return [kw for kw in keywords if kw.lower() in text_lower]


def extract_summary_snippets(text: str, keywords: list[str], max_snippets: int = 5, context_chars: int = 80) -> str:
    """从页面文本中提取关键词周围的上下文片段（摘要模式）"""
    text_lower = text.lower()
    snippets = []
    seen_positions = set()

    for kw in keywords:
        kw_lower = kw.lower()
        start = 0
        while len(snippets) < max_snippets:
            pos = text_lower.find(kw_lower, start)
            if pos == -1:
                break
            bucket = pos // context_chars
            if bucket in seen_positions:
                start = pos + len(kw_lower)
                continue
            seen_positions.add(bucket)

            snippet_start = max(0, pos - context_chars)
            snippet_end = min(len(text), pos + len(kw_lower) + context_chars)
            snippet = text[snippet_start:snippet_end].replace("\n", " ").strip()
            pattern = re.compile(re.escape(kw), re.IGNORECASE)
            snippet = pattern.sub(lambda m: f"**{m.group()}**", snippet)
            if snippet_start > 0:
                snippet = "..." + snippet
            if snippet_end < len(text):
                snippet = snippet + "..."
            snippets.append(snippet)
            start = pos + len(kw_lower)

    return "\n".join(f"  - {s}" for s in snippets) if snippets else ""


def extract_text(doc, pages: list[int] = None, search: str = None,
                 search_any: list[str] = None, markdown: bool = False,
                 summary: bool = False, max_results: int = 0) -> str:
    """提取文字内容，支持关键词过滤和摘要模式"""
    results = []
    page_range = pages if pages else range(len(doc))
    all_keywords = []
    if search:
        all_keywords = [search]
    elif search_any:
        all_keywords = search_any

    matched_count = 0

    for page_num in page_range:
        if page_num >= len(doc):
            continue
        if max_results > 0 and matched_count >= max_results:
            break

        page = doc[page_num]
        text = page.get_text("text")
        text_lower = text.lower()

        if all_keywords:
            matched_kws = _matches_any_keyword(text_lower, all_keywords)
            if not matched_kws:
                continue

        matched_count += 1

        if summary and all_keywords:
            snippets = extract_summary_snippets(text, all_keywords)
            if snippets:
                matched_str = ", ".join(_matches_any_keyword(text_lower, all_keywords))
                results.append(f"第 {page_num} 页 [匹配: {matched_str}]\n{snippets}")
        elif markdown:
            if all_keywords:
                for kw in all_keywords:
                    pattern = re.compile(re.escape(kw), re.IGNORECASE)
                    text = pattern.sub(lambda m: f"**{m.group()}**", text)
            results.append(f"### 第 {page_num} 页\n\n{text}")
        else:
            results.append(f"=== 第 {page_num} 页 (共 {len(doc)} 页) ===\n{text}")

    if not results:
        search_term = ", ".join(all_keywords) if all_keywords else ""
        if search_term:
            return f"未找到包含 '{search_term}' 的页面"
        return "无内容"

    if max_results > 0 and matched_count >= max_results:
        results.append(f"\n(已达到 --max-results {max_results} 上限，可能还有更多匹配页)")

    return "\n\n".join(results)


def extract_tables(doc, pages: list[int] = None) -> str:
    """提取表格（需要 PyMuPDF >= 1.23.0）"""
    results = []
    page_range = pages if pages else range(len(doc))

    for page_num in page_range:
        if page_num >= len(doc):
            continue
        page = doc[page_num]

        try:
            tables = page.find_tables()
            for i, table in enumerate(tables):
                data = table.extract()
                if not data:
                    continue
                results.append(f"=== 第 {page_num} 页 · 表格 {i+1} ===")
                if len(data) > 0:
                    header = data[0]
                    results.append("| " + " | ".join(str(c or "") for c in header) + " |")
                    results.append("| " + " | ".join("---" for _ in header) + " |")
                    for row in data[1:]:
                        results.append("| " + " | ".join(str(c or "") for c in row) + " |")
                results.append("")
        except AttributeError:
            results.append(f"(第 {page_num} 页: PyMuPDF 版本不支持表格提取，请升级到 >= 1.23.0)")

    return "\n".join(results) if results else "未检测到表格"


def extract_images(doc, output_dir: str, pages: list[int] = None) -> list[str]:
    """导出 PDF 中的图片"""
    os.makedirs(output_dir, exist_ok=True)
    saved = []
    page_range = pages if pages else range(len(doc))

    for page_num in page_range:
        if page_num >= len(doc):
            continue
        page = doc[page_num]
        images = page.get_images(full=True)

        for img_idx, img_info in enumerate(images):
            xref = img_info[0]
            try:
                pix = fitz.Pixmap(doc, xref)

                # 跳过太小的图片（可能是图标/装饰）
                if pix.width < 100 or pix.height < 100:
                    pix = None
                    continue

                # 转为 RGB（如果是 CMYK）
                if pix.n > 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)

                filename = f"page{page_num}_img{img_idx}.png"
                filepath = os.path.join(output_dir, filename)
                pix.save(filepath)
                saved.append(filepath)
                print(f"  已保存: {filepath} ({pix.width}x{pix.height})", file=sys.stderr)
                pix = None
            except Exception as e:
                print(f"  跳过: 第 {page_num} 页 图片 {img_idx}: {e}", file=sys.stderr)

    return saved


def get_metadata(doc) -> dict:
    """获取 PDF 元数据"""
    meta = doc.metadata
    return {
        "页数": len(doc),
        "标题": meta.get("title", ""),
        "作者": meta.get("author", ""),
        "主题": meta.get("subject", ""),
        "创建工具": meta.get("creator", ""),
        "创建日期": meta.get("createdDate", ""),
    }


def main():
    parser = argparse.ArgumentParser(description="PDF 阅读工具 — 提取文字、表格、图片")
    parser.add_argument("--file", required=True, help="PDF 文件路径")
    parser.add_argument("--pages", help="页码范围，如 '3-8' 或 '1,3,5-10'（从0开始）")
    parser.add_argument("--search", help="搜索关键词，只输出包含该词的页")
    parser.add_argument("--search-any", help="多关键词 OR 搜索（逗号分隔），返回匹配任一关键词的页")
    parser.add_argument("--summary", action="store_true", help="摘要模式：每页只输出匹配关键词周围的上下文片段（≤5条）")
    parser.add_argument("--max-results", type=int, default=20, help="最多返回 N 个匹配页（默认20）")
    parser.add_argument("--tables", action="store_true", help="提取表格")
    parser.add_argument("--extract-images", metavar="DIR", help="导出图片到指定目录")
    parser.add_argument("--metadata", action="store_true", help="只输出元数据")
    parser.add_argument("--markdown", action="store_true", help="输出 markdown 格式")
    parser.add_argument("--output", help="将结果追加到指定文件")
    parser.add_argument("--max-pages", type=int, default=50, help="最多处理的页数（默认50，防止超大PDF）")

    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"文件不存在: {args.file}", file=sys.stderr)
        sys.exit(1)

    doc = fitz.open(args.file)
    print(f"已打开: {args.file} ({len(doc)} 页)", file=sys.stderr)

    pages = None
    if args.pages:
        pages = parse_page_range(args.pages, len(doc))
    elif len(doc) > args.max_pages:
        print(f"PDF 超过 {args.max_pages} 页，只处理前 {args.max_pages} 页。用 --pages 指定范围或 --max-pages 调整。", file=sys.stderr)
        pages = list(range(args.max_pages))

    meta = get_metadata(doc)

    if args.metadata:
        for k, v in meta.items():
            print(f"{k}: {v}")
        doc.close()
        return

    if args.extract_images:
        saved = extract_images(doc, args.extract_images, pages)
        print(f"\n共导出 {len(saved)} 张图片到 {args.extract_images}", file=sys.stderr)
        for f in saved:
            print(f)
        doc.close()
        return

    search_any = None
    if args.search_any:
        search_any = [kw.strip() for kw in args.search_any.split(",") if kw.strip()]

    text = extract_text(doc, pages, args.search, search_any, args.markdown,
                        args.summary, args.max_results)

    tables_text = None
    if args.tables:
        tables_text = extract_tables(doc, pages)

    output = f"文件: {args.file} | 页数: {meta['页数']}\n"
    if meta["标题"]:
        output += f"标题: {meta['标题']}\n"
    output += f"\n{text}"
    if tables_text and args.tables:
        output += f"\n\n=== 表格 ===\n{tables_text}"

    print(output)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "a", encoding="utf-8") as f:
            f.write("\n\n" + output + "\n")
        print(f"\n已追加到: {args.output}", file=sys.stderr)

    doc.close()


if __name__ == "__main__":
    main()
