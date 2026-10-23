"""Vector PDF with embedded Korean fonts from the editable poster's layout."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm


def render(layout_path: Path, output: Path):
    data=json.loads(layout_path.read_text(encoding='utf-8'))
    pdfmetrics.registerFont(TTFont('PosterKR','C:/Windows/Fonts/malgun.ttf'))
    pdfmetrics.registerFont(TTFont('PosterKRBold','C:/Windows/Fonts/malgunbd.ttf'))
    width,height=[v*mm for v in data['physical_mm']]
    scale=width/data['width']
    output.parent.mkdir(parents=True,exist_ok=True)
    c=canvas.Canvas(str(output),pagesize=(width,height))
    c.setTitle('ClarifyTrial: 관측 근거와 위험 교정을 이용한 선택적 재확인')
    c.setAuthor('ClarifyTrial 연구진')
    c.scale(scale,scale)
    H=data['height']

    def text(copy,x,y,size=18,bold=False,color='#183249',width=None):
        font='PosterKRBold' if bold else 'PosterKR'
        c.setFont(font,size)
        c.setFillColor(HexColor(color))
        # Font ascent gives an explicit top anchor matching the authored grid.
        ascent=pdfmetrics.getAscent('PosterKRBold' if bold else 'PosterKR')*size/1000
        lines=[]
        for paragraph in copy.splitlines():
            current=''
            for word in paragraph.split(' '):
                candidate=(current+' '+word).lstrip()
                if width and current and pdfmetrics.stringWidth(candidate,font,size)>width:
                    lines.append(current);current=word
                else:current=candidate
            lines.append(current)
        for i,line in enumerate(lines):
            c.drawString(x,H-y-ascent-i*size*1.25,line)

    def line(x,y,w,color='#CBDBDF',weight=.7):
        c.setStrokeColor(HexColor(color));c.setLineWidth(weight)
        c.line(x,H-y,x+w,H-y)

    for e in data['elements']:
        x,y=e['x'],e['y']
        if e['kind']=='text':
            text(e['text'],x,y,e['size'],e['bold'],e['color'],e['w'])
        elif e['kind']=='rule':line(x,y,e['w'],e['color'],1.2)
        elif e['kind']=='table':
            row_height=e['h']/len(e['values'])
            for r,row in enumerate(e['values']):
                xx=x
                for j,value in enumerate(row):
                    highlight=r==len(e['values'])-1
                    c.setFillColor(HexColor('#183249' if r==0 else '#ECF7F6' if highlight else '#FFFFFF'))
                    c.setStrokeColor(HexColor('#CBDBDF'));c.setLineWidth(.6)
                    c.rect(xx,H-y-(r+1)*row_height,e['widths'][j],row_height,fill=1,stroke=1)
                    text(str(value),xx+10,y+r*row_height+7,16,r==0 or highlight,'#FFFFFF' if r==0 else '#183249')
                    xx+=e['widths'][j]
        elif e['kind']=='chart':
            # Quantitative evidence stays vector, with a visible zero baseline.
            left=x+122;plot_width=e['w']-180;top=y+15;bottom=y+e['h']-44
            bar_height=34
            for tick in range(0,int(e['max'])+1,20 if e['max']==100 else 300):
                xx=left+plot_width*tick/e['max']
                c.setStrokeColor(HexColor('#CBDBDF'));c.setLineWidth(.5)
                c.line(xx,H-top,xx,H-bottom)
                text(str(tick),xx-8,bottom+8,11,color='#4D6170')
            for i,(label,value,color) in enumerate(zip(('한 단계 선택','선택적 재확인'),e['values'],('#8C9AA5','#087F82'))):
                yy=top+12+i*60
                text(label,x,yy+7,14,color='#4D6170')
                c.setFillColor(HexColor(color))
                c.rect(left,H-yy-bar_height,plot_width*value/e['max'],bar_height,fill=1,stroke=0)
                shown=f'{value:.2f}' if e['format']=='0.00' else f'{value:,.0f}'
                text(shown,left+plot_width*value/e['max']+7,yy+4,19,True)
            text(e['unit'],left,bottom+27,12,color='#4D6170')
    c.showPage();c.save()
    print(f'Created one-page {data["physical_mm"][0]}x{data["physical_mm"][1]} mm PDF: {output}')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('layout',type=Path);parser.add_argument('output',type=Path)
    a=parser.parse_args();render(a.layout,a.output)
