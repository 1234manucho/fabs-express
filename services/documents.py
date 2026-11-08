"""Printable shipment documents generated from saved, authorised records."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import qrcode
from services.shipping import money, country

FONT=Path(__file__).resolve().parents[1]/'static'/'fonts'
for name,file in [('FX','DejaVuSans.ttf'),('FX-Bold','DejaVuSans-Bold.ttf')]:
    if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(FONT/file)))
pdfmetrics.registerFontFamily('FX',normal='FX',bold='FX-Bold',italic='FX',boldItalic='FX-Bold')
RED=HexColor('#ED3138');INK=HexColor('#121820');GREY=HexColor('#65707B');LINE=HexColor('#DFE3E8')
def styles():
    s=getSampleStyleSheet()
    for key in s.byName:s[key].fontName='FX';s[key].textColor=INK
    s['Normal'].fontSize=10;s['Normal'].leading=16
    s['Title'].fontName='FX-Bold';s['Title'].fontSize=28;s['Title'].textColor=RED;s['Title'].alignment=0;s['Title'].spaceAfter=18
    s['Heading2'].fontName='FX-Bold';s['Heading2'].fontSize=17;s['Heading2'].spaceAfter=14
    s.add(ParagraphStyle('Muted',fontName='FX',fontSize=8,leading=12,textColor=GREY))
    return s

def footer(c,doc):
    c.setStrokeColor(LINE);c.line(42,42,A4[0]-42,42);c.setFont('FX',8);c.setFillColor(GREY);c.drawString(42,27,'FabsExpress | Shipment document');c.drawRightString(A4[0]-42,27,f'Page {doc.page}')

def label_pdf(shipment,base):
    output=BytesIO();s=styles();doc=SimpleDocTemplate(output,pagesize=A4,leftMargin=42,rightMargin=42,topMargin=42,bottomMargin=60,title='FabsExpress shipment labels')
    p=lambda text,style='Normal':Paragraph(escape(str(text)),s[style])
    qr=qrcode.make(base+'/track?tracking='+shipment.tracking);buf=BytesIO();qr.save(buf,format='PNG');qr_data=buf.getvalue();story=[]
    for index,package in enumerate(shipment.packages,start=1):
        if index>1:story.append(PageBreak())
        story.extend([p('FabsExpress','Title'),p('SHIPMENT HANDLING LABEL','Heading2')])
        heading=Table([[p(shipment.tracking),Image(BytesIO(qr_data),width=95,height=95)],[p(f'Package {index} of {len(shipment.packages)} | {shipment.service.upper()}'),p('Scan to track','Muted')]],colWidths=[380,130])
        heading.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ALIGN',(1,0),(1,-1),'CENTER'),('BOTTOMPADDING',(0,0),(-1,-1),12)]));story.extend([heading,Spacer(1,20)])
        for title,addr in [('COLLECT FROM',shipment.origin),('DELIVER TO',shipment.destination)]:
            lines=[p(addr['name']),p(addr['address']),p(addr['city']+' '+addr.get('postal','')),p(country(addr['country'])),p(addr['phone'])]
            table=Table([[p(title,'Muted')],[lines]],colWidths=[510]);table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),HexColor('#F5F6F7')),('BOX',(0,0),(-1,-1),.6,LINE),('LEFTPADDING',(0,0),(-1,-1),16),('RIGHTPADDING',(0,0),(-1,-1),16),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10)]));story.extend([table,Spacer(1,16)])
        story.extend([p(f'Actual weight: {package.weight} kg | Dimensions: {package.length} x {package.width} x {package.height} cm'),Spacer(1,10),p('FabsExpress internal handling label. Carrier air waybills and customs documents are issued separately where required.','Muted')])
    doc.build(story,onFirstPage=footer,onLaterPages=footer);output.seek(0);return output

def invoice_pdf(shipment,config):
    output=BytesIO();doc=SimpleDocTemplate(output,pagesize=A4,leftMargin=42,rightMargin=42,topMargin=42,bottomMargin=60,title='FabsExpress shipping charges')
    s=styles();p=lambda text,style='Normal':Paragraph(escape(str(text)),s[style]);paid=shipment.payment_status=='paid'
    story=[p('FabsExpress','Title'),p('PAYMENT RECEIPT' if paid else 'SHIPPING QUOTATION','Heading2'),p(config['LEGAL_NAME']),p(config.get('BUSINESS_ADDRESS') or '')]
    if config.get('TAX_PIN'):story.append(p('Tax PIN: '+config['TAX_PIN']))
    story += [Spacer(1,20),p('Reference: '+shipment.tracking),p('Customer: '+shipment.user.name),p('From: '+shipment.origin['city']+', '+country(shipment.origin['country'])),p('To: '+shipment.destination['city']+', '+country(shipment.destination['country'])),Spacer(1,25)]
    details=shipment.quote_details or {};rows=[[p('CHARGE'),p('AMOUNT')]]
    for label,key in [('Transport','transport_minor'),('Fuel surcharge','fuel_minor'),('Handling','handling_minor'),('Shipping tax','tax_minor')]:rows.append([p(label),p(money(details.get(key,0),shipment.currency))])
    rows.append([p('TOTAL'),p(money(shipment.amount_minor,shipment.currency))])
    table=Table(rows,colWidths=[330,180]);table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),HexColor('#F5F6F7')),('BOTTOMPADDING',(0,0),(-1,-1),13),('TOPPADDING',(0,0),(-1,-1),13),('LINEBELOW',(0,1),(-1,-1),.5,LINE),('BACKGROUND',(0,-1),(-1,-1),HexColor('#FFF0F1'))]));story.extend([table,Spacer(1,18),p(details.get('duties','Customs duties and destination taxes are excluded.'))])
    if paid:
        for payment in shipment.payments:
            if payment.status=='paid':story.extend([Spacer(1,12),p('Verified payment: '+payment.provider.upper()),p('Receipt/reference: '+(payment.receipt or payment.public_id))])
    else:story.extend([Spacer(1,10),p('This quotation is not proof of payment.')])
    story.extend([Spacer(1,20),p('This document does not replace a customs commercial invoice or a statutory electronic tax invoice.','Muted')]);doc.build(story,onFirstPage=footer,onLaterPages=footer);output.seek(0);return output
