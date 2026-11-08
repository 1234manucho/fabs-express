from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
import pycountry
from models import db, Tariff, ShipmentEvent, AuditLog, MailOutbox
COUNTRIES = sorted([(c.alpha_2,c.name) for c in pycountry.countries], key=lambda c:c[1])
SERVICES = {'express':'Express delivery','economy':'Economy delivery','freight':'Freight & cargo'}
STATUS = {'quote_requested':'Quote requested','awaiting_payment':'Awaiting payment','booked':'Booking confirmed','collected':'Collected','warehouse':'At warehouse','in_transit':'In transit','customs':'Customs clearance','out_for_delivery':'Out for delivery','delivered':'Delivered','exception':'Shipment exception','cancelled':'Cancelled'}
TRANSITIONS = {
 'quote_requested':{'cancelled'}, 'awaiting_payment':{'cancelled'},
 'booked':{'collected','warehouse','in_transit','exception'}, 'collected':{'warehouse','in_transit','exception'},
 'warehouse':{'in_transit','customs','exception'}, 'in_transit':{'warehouse','customs','out_for_delivery','exception'},
 'customs':{'warehouse','in_transit','out_for_delivery','exception'}, 'out_for_delivery':{'delivered','exception','warehouse'},
 'exception':{'warehouse','in_transit','out_for_delivery'}, 'delivered':set(), 'cancelled':set()
}
def utc(dt): return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt
def iso(dt): return utc(dt).isoformat() if dt else None
def num(value,low=Decimal('0.001'),high=Decimal('100000'),places=3):
    try: v=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError): raise ValueError('Enter a valid number.')
    if not v.is_finite() or v<low or v>high or v.as_tuple().exponent < -places: raise ValueError(f'Number must be between {low} and {high}, with at most {places} decimal places.')
    return v

def minor(value): return int((num(value,Decimal('0'),Decimal('100000000'),2)*100).to_integral_value())
def money(value,currency='KES'): return f'{currency} {Decimal(value or 0)/100:,.2f}'
def country(code): return dict(COUNTRIES).get(code,code)
def text_field(data,key,minimum=1,maximum=160):
    val=str(data.get(key,'')).strip()
    if len(val)<minimum or len(val)>maximum: raise ValueError(f'{key.replace("_"," ").capitalize()} must contain {minimum}–{maximum} characters.')
    return val

def packages_from(data):
    weights=data.getlist('weight') if hasattr(data,'getlist') else data.get('weight',[])
    if not isinstance(weights,list): weights=[weights]
    if not 1<=len(weights)<=20: raise ValueError('Add between 1 and 20 packages.')
    items=[]
    for i,w in enumerate(weights):
        item={'weight':num(w,high=Decimal('2000'))}
        for field in ('length','width','height'):
            vals=data.getlist(field) if hasattr(data,'getlist') else data.get(field,[])
            if not isinstance(vals,list): vals=[vals]
            if len(vals)!=len(weights): raise ValueError('Each package needs weight and all three dimensions.')
            item[field]=num(vals[i],high=Decimal('1000'),places=2)
        items.append(item)
    return items

def calculate(tariff,packages):
    actual=sum(p['weight'] for p in packages)
    dimensional=sum(p['length']*p['width']*p['height']/tariff.divisor for p in packages)
    # Each package is independently assessed before summing the billable weights.
    billable=sum(max(p['weight'],p['length']*p['width']*p['height']/tariff.divisor) for p in packages)
    if billable>tariff.max_kg: return None
    steps=max(0,int(((billable-tariff.included_kg)/tariff.step_kg).to_integral_value(rounding=ROUND_CEILING)))
    transport=tariff.base_minor+steps*tariff.step_minor
    fuel=int((Decimal(transport)*tariff.fuel_bps/10000).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    handling=tariff.handling_minor
    tax=int((Decimal(transport+fuel+handling)*tariff.tax_bps/10000).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    return {'tariff_id':tariff.id,'tariff':tariff.name,'zone':tariff.zone,'currency':tariff.currency,'actual_kg':str(actual),'dimensional_kg':str(dimensional.quantize(Decimal('.001'))),'billable_kg':str(billable.quantize(Decimal('.001'))),'divisor':tariff.divisor,'transport_minor':transport,'fuel_minor':fuel,'handling_minor':handling,'tax_minor':tax,'total_minor':transport+fuel+handling+tax,'transit':f'{tariff.min_days}–{tariff.max_days} business days','duties':'Customs duties, destination taxes and exceptional charges are excluded; additional charges require customer approval.'}

def find_quote(origin,destination,service,packages,ship_date,origin_city='',destination_city=''):
    matches=db.session.scalars(db.select(Tariff).where(Tariff.active.is_(True),Tariff.origin==origin,Tariff.destination==destination,Tariff.service==service,Tariff.valid_from<=ship_date,Tariff.valid_to>=ship_date).order_by(Tariff.created_at.desc(),Tariff.id.desc())).all()
    # Specific city lanes override country-wide rates. Unknown lanes never inherit another city's tariff.
    matches=[r for r in matches if (not r.origin_city or r.origin_city.casefold()==origin_city.strip().casefold()) and (not r.destination_city or r.destination_city.casefold()==destination_city.strip().casefold())]
    matches.sort(key=lambda r:bool(r.origin_city)+bool(r.destination_city),reverse=True)
    for tariff in matches:
        quote=calculate(tariff,packages)
        if quote: return quote
    return None

def audit(action,object_id,actor=None,detail=None):
    db.session.add(AuditLog(action=action,object_id=str(object_id),actor_id=actor,detail=detail or {}))
def add_event(shipment,status,location,note,actor=None):
    shipment.status=status
    db.session.add(ShipmentEvent(shipment=shipment,status=status,location=location,note=note,actor_id=actor))
    audit('shipment.'+status,shipment.public_id,actor)
def notify_shipment(shipment,base_url):
    from worker import queue_or_send
    queue_or_send(MailOutbox(recipient=shipment.user.email,subject=f'FabsExpress {shipment.tracking}: {STATUS[shipment.status]}',body=f'Your shipment status is {STATUS[shipment.status]}.\n\nView the latest updates: {base_url}/shipments/{shipment.public_id}\n\nFabsExpress'))