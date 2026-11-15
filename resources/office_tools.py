"""Create editable Office artifacts using bundled libraries, no external service."""
import io
from pathlib import Path

def build(name,args):
    output=io.BytesIO();path=Path(args['path'])
    if name=='create_presentation':
        if path.suffix.lower()!='.pptx':raise ValueError('Для презентации укажите расширение .pptx')
        from pptx import Presentation
        from pptx.util import Inches,Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE
        prs=Presentation();prs.slide_width=Inches(13.333);prs.slide_height=Inches(7.5)
        cream=RGBColor(249,248,242);ink=RGBColor(38,46,36);sage=RGBColor(88,112,70);muted=RGBColor(103,112,96)
        def text(slide,value,x,y,w,h,size=22,color=ink,bold=False):
            frame=slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h)).text_frame;frame.word_wrap=True
            frame.margin_left=0;frame.margin_right=0
            for i,line in enumerate(str(value).split('\n')):
                p=frame.paragraphs[0] if i==0 else frame.add_paragraph();p.text=line;p.font.name='Arial';p.font.size=Pt(size);p.font.color.rgb=color;p.font.bold=bold;p.space_after=Pt(14)
            return frame
        slides=args.get('slides',[])
        if not 1<=len(slides)<=40:raise ValueError('Презентация должна содержать от 1 до 40 слайдов')
        for i,data in enumerate(slides):
            slide=prs.slides.add_slide(prs.slide_layouts[6]);slide.background.fill.solid();slide.background.fill.fore_color.rgb=cream
            cover=data.get('layout')=='cover' or i==0
            bar=slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,Inches(.65),Inches(.65),Inches(.52),Inches(.055));bar.fill.solid();bar.fill.fore_color.rgb=sage;bar.line.fill.background()
            text(slide,args.get('title','Devine')[:85],.65,.85,11,.4,11,muted)
            title=str(data.get('title',''))[:150]
            text(slide,title,.65,1.45 if cover else 1.35,11.8,1.7 if cover else 1.2,42 if cover and len(title)<85 else 32,sage if cover else ink,True)
            bullets=[str(x) for x in data.get('bullets',[])][:6]
            subtitle=str(data.get('subtitle',''))
            if cover:
                if subtitle:text(slide,subtitle,.7,3.5,10.9,1.2,23,muted)
                if bullets:text(slide,'\n'.join('• '+b[:220] for b in bullets[:3]),.7,4.85,11,1.8,19)
            else:
                right=data.get('right_bullets',[]);width=5.6 if right else 11.7
                y=2.7
                if subtitle:text(slide,subtitle,.7,2.55,11.5,.65,19,muted);y=3.3
                max_length=max([len(b) for b in bullets]+[0]);size=22 if max_length<120 and len(bullets)<=5 else 18
                text(slide,'\n'.join('• '+b[:350] for b in bullets),.7,y,width,6.6-y,size)
                if right:text(slide,'\n'.join('• '+str(b)[:350] for b in right[:6]),7,y,5.5,6.6-y,size)
            text(slide,'DEVINE',.7,7.02,2,.22,9,muted);text(slide,f'{i+1:02d} / {len(slides):02d}',11.5,7.02,1.1,.22,10,muted)
            if data.get('notes'):slide.notes_slide.notes_text_frame.text=str(data['notes'])
        prs.core_properties.title=args['title'];prs.core_properties.author='Devine';prs.save(output)
    elif name=='create_document':
        if path.suffix.lower()!='.docx':raise ValueError('Для документа укажите расширение .docx')
        from docx import Document
        from docx.shared import Inches,Pt,RGBColor
        doc=Document();doc.sections[0].top_margin=Inches(.8);doc.sections[0].bottom_margin=Inches(.8)
        style=doc.styles['Normal'];style.font.name='Arial';style.font.size=Pt(11);style.paragraph_format.space_after=Pt(9)
        doc.add_heading(str(args['title']),0)
        for section in args.get('sections',[])[:100]:
            if section.get('heading'):doc.add_heading(str(section['heading']),1)
            for paragraph in str(section.get('body','')).split('\n'):
                if paragraph:doc.add_paragraph(paragraph)
            for bullet in section.get('bullets',[]):doc.add_paragraph(str(bullet),style='List Bullet')
        doc.core_properties.author='Devine';doc.save(output)
    else:
        if path.suffix.lower()!='.xlsx':raise ValueError('Для таблицы укажите расширение .xlsx')
        from openpyxl import Workbook
        from openpyxl.styles import Font,PatternFill,Alignment
        from openpyxl.utils import get_column_letter
        wb=Workbook();ws=wb.active;ws.title=str(args.get('sheet','Данные'))[:31];headers=args['headers'];rows=args['rows']
        if len(headers)>100 or len(rows)>10000:raise ValueError('Лимит: 100 столбцов и 10 000 строк')
        ws.append(headers)
        for cell in ws[1]:cell.font=Font(name='Calibri',bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='526D3F');cell.alignment=Alignment(wrap_text=True)
        for row in rows:ws.append(row)
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for col in range(1,len(headers)+1):
            length=max([len(str(headers[col-1]))]+[len(str(row[col-1])) for row in rows[:200] if len(row)>=col]);ws.column_dimensions[get_column_letter(col)].width=min(55,max(14,length+3))
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.font=Font(name='Calibri',size=11);cell.alignment=Alignment(vertical='top',wrap_text=True)
                if cell.row%2==0:cell.fill=PatternFill('solid',fgColor='F0F4EA')
        wb.save(output)
    return output.getvalue()
