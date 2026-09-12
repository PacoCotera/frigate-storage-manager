import test from 'node:test';
import assert from 'node:assert/strict';
import {cleanupBlockers,resetBlockers,confirmationText} from '../frigate_storage_manager/fsm/static/maintenance.mjs';

const state={destructive_enabled:true,is_admin:true};
const readiness={ready:true,blockers:[]};
const approved={target:'test_frigate',result:{counts:{recordings:4,event:1,reviewsegment:1},bytes:1000}};
const blockers=(s=state,t='test_frigate',r=readiness,a=approved,busy=false)=>cleanupBlockers(s,t,r,a,busy);

test('a validated, authorized fresh selection can be confirmed',()=>{
  assert.deepEqual(blockers(),[]);
  assert.match(blockers({...state,is_admin:false}).join(' '),/admin_user_ids/);
  assert.match(blockers(state,null).join(' '),/Validate/);
  assert.match(blockers(state,'other_frigate').join(' '),/Validate/);
  assert.match(blockers(state,'test_frigate',{ready:false,blockers:['Turn off Watchdog.']}).join(' '),/Watchdog/);
});

test('busy, expired, empty and missing previews cannot enable delete',()=>{
  assert.ok(blockers({...state,worker_active:true}).length);
  assert.ok(blockers({...state,recovery_required:true}).length);
  assert.ok(blockers({...state,destructive_enabled:false}).length);
  assert.ok(blockers(state,'test_frigate',readiness,approved,true).length);
  assert.ok(blockers(state,'test_frigate',readiness,null).length);
  assert.ok(blockers(state,'test_frigate',readiness,{...approved,expired:true}).length);
  assert.ok(blockers(state,'test_frigate',readiness,{...approved,expires_at:1}).length);
  assert.ok(blockers(state,'test_frigate',readiness,{...approved,result:{counts:{recordings:0}}}).length);
});

test('confirmation identifies the exact installation, selection and permanent result',()=>{
  const text=confirmationText(approved,'front · before Jan 1 · exports kept',n=>`${n} B`);
  for(const value of ['test_frigate','front · before Jan 1 · exports kept','1000 B','4 recording segments','1 events','Frigate will pause','Deleted video cannot be restored']) assert.ok(text.includes(value));
});

test('full reset requires authorization and validation but no camera or date preview',()=>{
  assert.deepEqual(resetBlockers(state,'test_frigate',readiness),[]);
  assert.ok(resetBlockers({...state,is_admin:false},'test_frigate',readiness).length);
  assert.ok(resetBlockers(state,null,null).length);
  assert.ok(resetBlockers(state,'test_frigate',readiness,true).length);
  assert.equal(resetBlockers({...state,worker_active:true},null,null).length,1);
});
