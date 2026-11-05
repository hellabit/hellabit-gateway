"""Stable external identities retain the complete provider/account scope."""
from .ledger import digest,identifier

def event_key(tenant,connection,event_id,kind="adjustment"):
    identifier(tenant);identifier(connection);identifier(event_id)
    return "ev_"+digest({"schema":"hellabit.external-event.v1","workspace":tenant,
                         "connection":connection,"kind":kind,"id":event_id})
