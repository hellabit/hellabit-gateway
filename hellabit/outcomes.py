"""Current financial outcomes sit alongside immutable settlement history."""
AWAITING_SETTLEMENT = "state='unknown' AND accepted_reference IS NOT NULL AND last_error IS NULL"
AMBIGUOUS = f"state='unknown' AND NOT ({AWAITING_SETTLEMENT})"
INTENT_STATE = f"CASE WHEN {AWAITING_SETTLEMENT} THEN 'awaiting_settlement' ELSE state END"

def totals(db,tenant,intent,settled):
    row=db.execute("SELECT COALESCE(SUM(amount),0) FROM payment_adjustments WHERE tenant=? AND intent=?",(tenant,intent)).fetchone()
    returned=row[0]
    latest=db.execute("""SELECT event_key,event_id,kind,amount,reference,connection,created FROM payment_adjustments
        WHERE tenant=? AND intent=? ORDER BY created DESC,rowid DESC LIMIT 1""",(tenant,intent)).fetchone()
    return {"original_settled_units":settled,"returned_units":returned,"net_paid_units":settled-returned,
            "effective_outcome":"fully-returned" if settled and returned==settled else
                "partially-returned" if returned else "paid" if settled else None,
            "latest_adjustment":dict(latest) if latest else None}

def intent_view(db,row):
    data={k:v for k,v in dict(row).items() if k!="lease_hash"}
    if row['state']=='unknown' and row['accepted_reference'] is not None and row['last_error'] is None:
        data['state']='awaiting_settlement'
    current=totals(db,row['tenant'],row['id'],row['amount'] if row['state']=='confirmed' else 0)
    current['effective_outcome']=current['effective_outcome'] or data['state']
    return {**data,**current}

def payout_view(db,row):
    intent=db.execute(f"SELECT id,{INTENT_STATE} AS display_state FROM execution_intents WHERE tenant=? AND payout=?",
                      (row['tenant'],row['id'])).fetchone()
    current=totals(db,row['tenant'],intent[0] if intent else '',row['amount'] if row['state']=='paid' else 0)
    current['effective_outcome']=current['effective_outcome'] or row['state']
    data={k:v for k,v in dict(row).items() if k not in ('key_hash','request_hash')}
    return {**data,**current,'intent_id':intent['id'] if intent else None,
            'intent_state':intent['display_state'] if intent else None}
