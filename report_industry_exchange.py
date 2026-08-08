#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shutil
import csv
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

def get_target_date():
    now = datetime.now()
    current_hour = now.hour
    if current_hour < 15:
        target_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        target_date = now.strftime('%Y%m%d')
    return target_date

def get_report_folder_name():
    target_date = get_target_date()
    folder_name = f"同花顺行业资金流向{target_date}"
    return folder_name

def create_report_folder():
    folder_name = get_report_folder_name()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_path = os.path.join(script_dir, folder_name)

    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        print(f"🗑️ 已删除旧文件夹: {folder_name}")

    os.makedirs(folder_path)
    print(f"📁 创建文件夹: {folder_name}")

    return folder_path

def read_industry_data(days=10):
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    target_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        ts_code,
        name,
        SUM(net_buy_amount) as total_net_buy,
        SUM(net_sell_amount) as total_net_sell,
        SUM(net_amount) as total_net_amount,
        COUNT(*) as days_count,
        GROUP_CONCAT(DISTINCT lead_stock SEPARATOR ',') as lead_stock
    FROM ths_industry_daily_t
    WHERE trade_date >= %s AND trade_date <= %s
    GROUP BY ts_code, name
    ORDER BY total_net_amount DESC
    LIMIT 10
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (start_date, target_date))
            results = cursor.fetchall()
        connection.commit()
        print(f"✅ 成功读取 {len(results)} 个行业数据 ({start_date} ~ {target_date})")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return []
    finally:
        close_connection(connection)

def generate_csv_file(top_industries, folder_path):
    target_date = get_target_date()
    csv_filename = f"行业领涨股票{target_date}.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    csv_data = []
    for industry in top_industries:
        lead_stock = industry.get('lead_stock', '')
        csv_data.append([lead_stock])

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['领涨股票'])
        writer.writerows(csv_data)

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_data

def generate_pdf_report(top_industries, folder_path, days=10):
    if not HAS_REPORTLAB:
        print("❌ 缺少 reportlab 库，请先安装: pip install reportlab")
        return None

    target_date = get_target_date()
    report_filename = f"行业资金流向分析报告{target_date}.pdf"
    report_path = os.path.join(folder_path, report_filename)

    pdfmetrics.registerFont(TTFont('SimHei', '/System/Library/Fonts/STHeiti Light.ttc'))

    doc = SimpleDocTemplate(
        report_path,
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontName='SimHei',
        fontSize=18,
        alignment=1,
        spaceAfter=20
    )
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontName='SimHei',
        fontSize=10
    )

    elements = []

    title = Paragraph(f"行业资金流向分析报告", title_style)
    elements.append(title)

    now = datetime.now()
    report_date = target_date
    start_date = (now - timedelta(days=days)).strftime('%Y%m%d')

    info_text = Paragraph(f"报告日期: {report_date}<br/>分析周期: {start_date} ~ {report_date} (近{days}个交易日)", normal_style)
    elements.append(info_text)
    elements.append(Spacer(1, 20))

    table_data = [['排名', '行业名称', '累计流入(亿元)', '累计流出(亿元)', '累计净额(亿元)', '交易天数']]

    for i, industry in enumerate(top_industries, 1):
        table_data.append([
            str(i),
            industry['name'],
            f"{float(industry['total_net_buy']):.4f}",
            f"{float(industry['total_net_sell']):.4f}",
            f"{float(industry['total_net_amount']):.4f}",
            str(industry['days_count'])
        ])

    col_widths = [1.5*cm, 3*cm, 3*cm, 3*cm, 3*cm, 2*cm]
    table = Table(table_data, colWidths=col_widths)

    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.2, 0.4, 0.6)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'SimHei'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.Color(0.95, 0.95, 0.95)),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.Color(0.93, 0.93, 0.93)]),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 30))

    total_net_buy = sum(float(i['total_net_buy']) for i in top_industries)
    total_net_sell = sum(float(i['total_net_sell']) for i in top_industries)
    total_net_amount = sum(float(i['total_net_amount']) for i in top_industries)

    summary_text = Paragraph(
        f"<b>汇总分析:</b><br/>"
        f"• 共 {len(top_industries)} 个行业入选前10<br/>"
        f"• 合计流入: {total_net_buy:.4f} 亿元<br/>"
        f"• 合计流出: {total_net_sell:.4f} 亿元<br/>"
        f"• 合计净额: {total_net_amount:.4f} 亿元",
        normal_style
    )
    elements.append(summary_text)
    elements.append(Spacer(1, 30))

    lead_stock_title = Paragraph("<b>前10行业领涨股票</b>", normal_style)
    elements.append(lead_stock_title)
    elements.append(Spacer(1, 10))

    lead_stock_table_data = [['排名', '行业名称', '领涨股票']]
    for i, industry in enumerate(top_industries, 1):
        lead_stock = industry.get('lead_stock', '')
        lead_stock_table_data.append([
            str(i),
            industry['name'],
            lead_stock
        ])

    lead_stock_col_widths = [1.5*cm, 2.5*cm, 9*cm]
    lead_stock_table = Table(lead_stock_table_data, colWidths=lead_stock_col_widths)

    lead_stock_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.2, 0.5, 0.3)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'SimHei'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('TOPPADDING', (0, 1), (-1, -1), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.Color(0.95, 0.95, 0.95)),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.Color(0.93, 0.93, 0.93)]),
    ]))

    elements.append(lead_stock_table)

    doc.build(elements)
    print(f"✅ PDF报告已生成: {report_path}")

    return report_path

def main():
    print("=" * 80)
    print("行业资金流向分析报告生成")
    print("=" * 80)

    folder_path = create_report_folder()

    top_industries = read_industry_data(days=10)

    if not top_industries:
        print("❌ 没有获取到数据，退出程序")
        return

    csv_data = generate_csv_file(top_industries, folder_path)

    report_path = generate_pdf_report(top_industries, folder_path, days=10)

    if report_path:
        print("\n" + "=" * 80)
        print(f"🎉 报告生成完成！")
        print(f"📁 文件夹路径: {folder_path}")
        print(f"📄 报告路径: {report_path}")
        print("=" * 80)

if __name__ == "__main__":
    main()