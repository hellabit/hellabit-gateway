#!/usr/bin/env python3
"""One scoped operator command; no provider credentials or live submission path."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def owner_file(path,limit=100000):
    path=Path(path)
    if (path.is_symlink() or not path.is_file() or path.stat().st_uid!=os.getuid() or
            stat.S_IMODE(path.stat().st_mode)!=0o600 or path.stat().st_size>limit):
        raise ValueError("owner_file_required")
    return path.read_bytes()

def main():
    env={"PATH":os.defpath,"LANG":"en_US.UTF-8","PYTHONPATH":str(ROOT),"PYTHONDONTWRITEBYTECODE":"1"}
    config_path=ROOT/".private/toolchain.json"
    if config_path.exists():
        config=json.loads(owner_file(config_path))
        record=config.get("apiPython")
        if record:
            python=Path(record["path"])
            good=(not python.is_symlink() and python.is_file() and
                  hashlib.sha256(python.read_bytes()).hexdigest()==record["sha256"] and
                  hashlib.sha256((ROOT/"requirements-api.txt").read_bytes()).hexdigest()==config.get("apiRequirementsSha256"))
            if not good:raise SystemExit("Optional API toolchain changed; rerun its bounded bootstrap.")
            if Path(sys.executable).resolve()!=python.resolve():
                os.execve(python,[str(python),str(Path(__file__).resolve()),*sys.argv[1:]],env)
    sys.path.insert(0,str(ROOT))
    from hellabit.ledger import Ledger,Error,canonical
    from hellabit.readiness import Readiness
    from hellabit.operations import Operations
    from hellabit.fixtures import SandboxProvider
    from hellabit.backup import _write_new
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state",type=Path,default=ROOT/".private/ledger.sqlite3")
    parser.add_argument("--tenant")
    commands=parser.add_subparsers(dest="command",required=True)
    commands.add_parser("status");commands.add_parser("drill")
    history=commands.add_parser("export");history.add_argument("--output",type=Path,required=True)
    report=commands.add_parser("payout-report");report.add_argument("--output",type=Path,required=True)
    tick=commands.add_parser("tick");tick.add_argument("--intent")
    reconcile=commands.add_parser("reconcile");reconcile.add_argument("--intent",required=True)
    review=commands.add_parser("reference-case")
    review.add_argument("--intent",required=True);review.add_argument("--output",type=Path,required=True)
    resolve=commands.add_parser("resolve-reference-conflict")
    resolve.add_argument("--intent",required=True);resolve.add_argument("--reviewer",required=True)
    resolve.add_argument("--expected-case-sha256",required=True)
    packet=commands.add_parser("packet");packet.add_argument("--output",type=Path,required=True)
    evidence=commands.add_parser("record-evidence")
    for name in ("control","reference","document-hash"):evidence.add_argument("--"+name,required=True)
    evidence.add_argument("--expires",type=int,required=True)
    evidence.add_argument("--scope-sha256")
    profile=commands.add_parser("set-evidence-profile")
    profile.add_argument("--profile-sha256",required=True);profile.add_argument("--expected-scope-sha256",required=True)
    revoke=commands.add_parser("revoke-evidence")
    for name in ("control","reference","reason"):revoke.add_argument("--"+name,required=True)
    usage=commands.add_parser("usage");usage.add_argument("action",choices=["plan","record","preview","close"])
    usage.add_argument("--plan",required=True);usage.add_argument("--currency",default="USD",choices=["CAD","USD"])
    usage.add_argument("--rate-micro",type=int,default=1000);usage.add_argument("--base-minor",type=int,default=0)
    usage.add_argument("--key");usage.add_argument("--quantity",type=int,default=1);usage.add_argument("--period")
    usage.add_argument("--expected-due-minor",type=int)
    identity=commands.add_parser("identity");identity.add_argument("action",choices=["grant","revoke","renew","replace","reinstate"])
    identity.add_argument("--binding",type=Path,required=True)
    identity.add_argument("--grant",type=Path);identity.add_argument("--principal")
    identity.add_argument("--expected-version",type=int)
    agent=commands.add_parser('agent-policy');agent.add_argument('action',choices=['set','approve','renew','adopt'])
    agent.add_argument('--principal',required=True);agent.add_argument('--file',type=Path)
    agent.add_argument('--expected-version',type=int);agent.add_argument('--quote');agent.add_argument('--key')
    agent.add_argument('--expected-expiry',type=int);agent.add_argument('--operation')
    cancel=commands.add_parser("cancel-unknown");cancel.add_argument("--intent",required=True)
    index=commands.add_parser("index");index.add_argument("action",choices=["bind","status","sync","events"])
    index.add_argument("--config",type=Path,required=True);index.add_argument("--max-blocks",type=int,default=20)
    index.add_argument("--cursor");index.add_argument("--limit",type=int,default=1000)
    backup=commands.add_parser("backup");backup.add_argument("action",choices=["keygen","create","restore"])
    backup.add_argument("--key-file",type=Path);backup.add_argument("--input",type=Path);backup.add_argument("--output",type=Path,required=True)
    recover=commands.add_parser('recover-handover')
    recover.add_argument('--authoritative-state',type=Path,required=True)
    recover.add_argument('--provider-state',type=Path,required=True)
    recover.add_argument('--output',type=Path,required=True)
    recover.add_argument('--seal-to',type=Path);recover.add_argument('--key-file',type=Path)
    sealed=commands.add_parser('recover-sealed');sealed.add_argument('--input',type=Path,required=True)
    sealed.add_argument('--key-file',type=Path,required=True);sealed.add_argument('--provider-state',type=Path,required=True)
    sealed.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.command=="backup" and args.action=="keygen":
            _write_new(args.output,secrets.token_bytes(32));result={"key_created":True,"key_bytes_in_output":False}
        elif args.command=="backup" and args.action=="restore":
            from hellabit.backup import restore
            if args.key_file is None or args.input is None:raise Error("backup_input_and_key_required",400)
            result=restore(args.input,args.output,owner_file(args.key_file,32))
        elif args.command=='recover-sealed':
            from hellabit.recovery import resume_sealed
            result=resume_sealed(args.input,owner_file(args.key_file,32),SandboxProvider(args.provider_state,require_existing=True),args.output)
        else:
            if args.command in ("payout-report","reference-case","resolve-reference-conflict") and not args.state.is_file():
                code = "payout_report_state_required" if args.command == "payout-report" else "operator_state_required"
                raise Error(code,404)
            book=Ledger(args.state)
            if args.command=='recover-handover':
                from hellabit.recovery import handover
                if not args.authoritative_state.is_file() or not args.provider_state.is_file():
                    raise Error('intact_authoritative_history_required',423)
                if bool(args.seal_to)!=bool(args.key_file):raise Error('sealed_archive_and_key_required',400)
                result=handover(book,Ledger(args.authoritative_state),SandboxProvider(args.provider_state,require_existing=True),args.output,
                                sealed_archive=args.seal_to,key=owner_file(args.key_file,32) if args.key_file else None)
            elif args.command=="backup":
                from hellabit.backup import create
                if args.key_file is None:raise Error("backup_key_required",400)
                result=create(book,args.output,owner_file(args.key_file,32))
            else:
                tenant=args.tenant
                if tenant is None:
                    with book.db() as db:rows=db.execute("SELECT id FROM tenants LIMIT 2").fetchall()
                    if len(rows)!=1:raise Error("workspace_selection_required",400)
                    tenant=rows[0][0]
                if args.command=="status":result=Readiness(book).report(tenant)
                elif args.command=="export":
                    from hellabit.history import export_history
                    result=export_history(book,tenant,args.output)
                elif args.command=="payout-report":
                    from hellabit.history import export_payout_report
                    result=export_payout_report(book,tenant,args.output)
                elif args.command=="reference-case":
                    case=Operations(book).inspect_reference_conflict(tenant,args.intent)
                    _write_new(args.output,canonical(case).encode())
                    result={key:case[key] for key in ("case_sha256","resolution_available")}
                elif args.command=="resolve-reference-conflict":
                    row=Operations(book).resolve_reference_conflict(
                        tenant,args.intent,args.expected_case_sha256,args.reviewer,
                    )
                    result={"intent":row["id"],"state":row["state"],"replayed":row.get("replayed",False)}
                elif args.command=="packet":
                    packet=Readiness(book).packet(tenant);_write_new(args.output,canonical(packet).encode())
                    result={"packet_written":True,"identity_details_included":False,"legal_determination_included":False}
                elif args.command=="record-evidence":
                    result=Readiness(book).record(tenant,args.control,args.reference,args.document_hash,args.expires,args.scope_sha256)
                elif args.command=="set-evidence-profile":
                    result=Readiness(book).set_profile(tenant,args.profile_sha256,args.expected_scope_sha256)
                elif args.command=="revoke-evidence":
                    result=Readiness(book).revoke(tenant,args.control,args.reference,args.reason)
                elif args.command=="drill":
                    from hellabit.backup import recovery_drill
                    result=recovery_drill(book)
                elif args.command=='agent-policy':
                    from hellabit.agent_policy import AgentPolicy
                    from hellabit.rpc import _strict_object,_invalid_constant
                    policy=AgentPolicy(book)
                    if args.action=='set':
                        if args.file is None or args.expected_version is None:raise Error('policy_file_and_expected_version_required',400)
                        terms=json.loads(owner_file(args.file,8192),object_pairs_hook=_strict_object,parse_constant=_invalid_constant)
                        result=policy.configure(tenant,args.principal,terms,args.expected_version)
                    elif args.action=='adopt':
                        if not args.operation:raise Error('exact_held_operation_required',400)
                        result=policy.adopt(tenant,args.principal,args.operation)
                    else:
                        if not args.quote or not args.key:raise Error('exact_quote_and_purchase_key_required',400)
                        if args.action=='renew' and args.expected_expiry is None:
                            raise Error('expected_approval_expiry_required',400)
                        result=policy.approve(tenant,args.principal,args.quote,args.key,
                                              renew_expiry=args.expected_expiry if args.action=='renew' else None)
                elif args.command=="identity":
                    from hellabit.api import load_config
                    from hellabit.identity import Identity
                    from hellabit.rpc import _strict_object,_invalid_constant
                    cfg=load_config(args.binding)
                    if cfg["state"].resolve()!=book.path.resolve():raise Error("identity_state_binding_mismatch",400)
                    authority=Identity(book,issuer=cfg["issuer"],audience=cfg["audience"],public_keys=cfg["public_keys"])
                    if args.action=="revoke":
                        if not args.principal:raise Error("principal_reference_required",400)
                        result=authority.revoke(tenant,args.principal)
                    elif args.action=="grant":
                        if args.grant is None:raise Error("owner_grant_file_required",400)
                        grant=json.loads(owner_file(args.grant),object_pairs_hook=_strict_object,parse_constant=_invalid_constant)
                        required={"subject","role","expires"}
                        if not isinstance(grant,dict) or not required<=set(grant) or set(grant)-required-{"customer","rate_limit"}:
                            raise Error("invalid_principal_grant_fields",400)
                        result=authority.enroll(tenant,**grant)
                    else:
                        if not args.principal or args.expected_version is None or args.grant is None:
                            raise Error("principal_version_and_grant_required",400)
                        grant=json.loads(owner_file(args.grant),object_pairs_hook=_strict_object,parse_constant=_invalid_constant)
                        if not isinstance(grant,dict) or "expires" not in grant or set(grant)-{"expires","role","customer","rate_limit"}:
                            raise Error("invalid_principal_grant_fields",400)
                        result=authority.change_grant(tenant,args.principal,args.action,args.expected_version,**grant)
                elif args.command=="index":
                    from hellabit.indexer import EventIndexer,MAX_LOG_RESPONSE_BYTES,evm_address
                    from hellabit.rpc import ReadOnlyRpc,_strict_object,_invalid_constant
                    cfg=json.loads(owner_file(args.config),object_pairs_hook=_strict_object,parse_constant=_invalid_constant)
                    required={"binding","chain_id","contract","start_block","endpoint","allowed_hosts"}
                    if not isinstance(cfg,dict) or set(cfg)!=required:raise Error("invalid_indexer_binding",400)
                    cfg["contract"]=evm_address(cfg["contract"])
                    projection=EventIndexer(book)
                    if args.action=="bind":
                        projection.bind(tenant,cfg["binding"],cfg["chain_id"],cfg["contract"],cfg["start_block"])
                        result={"bound":True,"network_requests":0,"financial_postings":0}
                    elif args.action=="status":
                        row=projection.state(tenant,cfg["binding"])
                        result={k:row[k] for k in ("chain_id","cursor","finalized","halted","updated")}
                    else:
                        row=projection.state(tenant,cfg["binding"])
                        if any(row[k]!=cfg[k] for k in ("chain_id","contract","start_block")):
                            raise Error("chain_binding_immutable",409)
                        if args.action=="events":
                            result=projection.event_page(tenant,cfg["binding"],cursor=args.cursor,limit=args.limit)
                        else:
                            rpc=ReadOnlyRpc(cfg["endpoint"],allowed_hosts=cfg["allowed_hosts"],chain_id=cfg["chain_id"],
                                            max_bytes=MAX_LOG_RESPONSE_BYTES)
                            synced=projection.sync(tenant,cfg["binding"],rpc,args.max_blocks)
                            result={k:synced[k] for k in ("chain_id","cursor","finalized","halted","financial_postings")}
                elif args.command in ("tick","reconcile","cancel-unknown"):
                    ops=Operations(book);provider=SandboxProvider.for_ledger(book)
                    if args.command=="cancel-unknown":
                        row=ops.cancel_unknown(tenant,args.intent,provider);result={"intent":row["id"],"state":row["state"]}
                    elif args.command=="reconcile":
                        row=ops.reconcile(tenant,args.intent,provider)
                        result={'intent':row['id'],**{k:row[k] for k in ('state','original_settled_units','returned_units','net_paid_units','effective_outcome','latest_adjustment')}}
                    else:
                        result=ops.tick(tenant,provider,intent=args.intent)
                elif args.command=="usage":
                    from hellabit.metering import UsageMeter
                    meter=UsageMeter(book)
                    if args.action=="plan":result=meter.configure(tenant,args.plan,args.currency,args.rate_micro,args.base_minor)
                    elif args.action=="record":
                        if not args.key:raise Error("stable_usage_key_required",400)
                        result=meter.record(tenant,args.plan,args.key,args.quantity)
                    elif args.action=="preview":result=meter.preview(tenant,args.plan,args.period)
                    else:
                        if not args.period:raise Error("billing_period_required",400)
                        result=meter.close(tenant,args.plan,args.period,expected_due_minor=args.expected_due_minor)
        print(canonical({"schema":"hellabit.operator-result.v1","ok":True,"action":args.command,
                         "live_payments_enabled":False,"result":result}))
        return 0
    except ImportError:
        print(canonical({"ok":False,"error":"optional_api_tools_not_configured","live_payments_enabled":False}));return 1
    except Error as error:
        print(canonical({"ok":False,"error":error.code,"live_payments_enabled":False}));return 1
    except (ValueError,KeyError,OSError):
        print(canonical({"ok":False,"error":"operator_configuration_or_file_invalid","live_payments_enabled":False}));return 1

if __name__=="__main__":raise SystemExit(main())
