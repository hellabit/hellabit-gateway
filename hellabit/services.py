"""Bounded pure-computation examples. No network or external side effects.

A handler executes inside the result-and-charge transaction. Network services
need a separate durable worker and upstream idempotency/status contract; they
must not be inserted into this registry and described as exactly-once work.
"""
from .ledger import Error, MAX_AMOUNT, units


def invoice_input(value):
    required = {'currency', 'items', 'tax_units', 'declared_total_units'}
    if not isinstance(value, dict) or set(value) != required or value['currency'] not in ('CAD', 'USD'):
        raise Error('invalid_invoice_input', 400)
    items = value['items']
    if not isinstance(items, list) or not 1 <= len(items) <= 50:
        raise Error('invoice_requires_1_to_50_items', 400)
    normalized = []
    subtotal = 0
    for item in items:
        if not isinstance(item, dict) or set(item) != {'quantity', 'unit_amount_units'}:
            raise Error('invalid_invoice_line', 400)
        quantity = item['quantity']
        if type(quantity) is not int or not 1 <= quantity <= 1000:
            raise Error('invalid_invoice_quantity', 400)
        unit_amount = units(item['unit_amount_units'], zero=True)
        subtotal += quantity * unit_amount
        if subtotal > MAX_AMOUNT:
            raise Error('invoice_total_limit', 400)
        normalized.append({'quantity':quantity, 'unit_amount_units':unit_amount})
    tax, declared = units(value['tax_units'], zero=True), units(value['declared_total_units'], zero=True)
    if subtotal + tax > MAX_AMOUNT:
        raise Error('invoice_total_limit', 400)
    return {'currency':value['currency'], 'items':normalized, 'tax_units':tax, 'declared_total_units':declared}


def check_invoice(value):
    value = invoice_input(value)
    subtotal = sum(item['quantity'] * item['unit_amount_units'] for item in value['items'])
    total = subtotal + value['tax_units']
    return {'schema':'hellabit.invoice-total-check.v1', 'currency':value['currency'], 'decimals':2,
            'line_count':len(value['items']), 'subtotal_units':subtotal, 'tax_units':value['tax_units'],
            'computed_total_units':total, 'declared_total_units':value['declared_total_units'],
            'difference_units':value['declared_total_units']-total,
            'matches_declared_total':value['declared_total_units']==total,
            'external_requests':0, 'scope':'arithmetic-only', 'invoice_authenticity_verified':False}


def prepare_input(resource, value):
    if resource == 'invoice-check':
        return invoice_input(value)
    if resource == 'report' and value is None:
        return None
    raise Error('resource_or_input_not_supported', 400)


def run_service(resource, value):
    if resource == 'invoice-check':
        return check_invoice(value)
    if resource == 'report' and value is None:
        return {'message':'Synthetic reference output. No external data was purchased.', 'external_requests':0}
    raise Error('resource_or_input_not_supported', 400)
