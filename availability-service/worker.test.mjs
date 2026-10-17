import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import worker, {SCHEMA,validate} from './worker.mjs';

const token = 'test-only-not-a-real-secret-000000000';
function database() {
  const sqlite = new DatabaseSync(':memory:'); sqlite.exec(SCHEMA);
  return {prepare(sql) {let args = []; return {
    bind(...values) {args = values; return this;},
    async run() {const r=sqlite.prepare(sql).run(...args); return {meta:{changes:Number(r.changes)}};},
    async first() {return sqlite.prepare(sql).get(...args) || null;}
  };}};
}
const stamp = offset => new Date(Date.now()+offset).toISOString().replace(/\.\d{3}Z$/,'Z');
function payload(overrides={}) {
  return {schema_version:1,facility:'hiranuma',checked_at:stamp(0),result:'ok',
    cells:[{room:'会議室A',date:'2026-10-20',period:'午前',status:'available_in_source'}],...overrides};
}
const env = () => ({DB:database(),INGEST_TOKEN:token,APP_ORIGIN:'https://keiko-note-9718d.web.app'});
const ingest = (data, secret=token) => new Request('https://example.test/ingest',{
  method:'POST',headers:{Authorization:'Bearer '+secret},body:JSON.stringify(data)});
const read = facility => new Request('https://example.test/availability?facility='+facility);

test('authorization and missing configuration fail closed',async()=>{
  assert.equal((await worker.fetch(ingest(payload(),'wrong'),env())).status,401);
  assert.equal((await worker.fetch(ingest(payload()),{DB:database()})).status,503);
  assert.equal((await worker.fetch(read('hiranuma'),{})).status,503);
});
test('real SQLite upsert, duplicate delivery, error, recovery and old delivery',async()=>{
  const e=env(); const good=payload();
  assert.equal((await worker.fetch(ingest(good),e)).status,200);
  assert.equal((await (await worker.fetch(ingest(good),e)).json()).updated,false);
  assert.equal((await (await worker.fetch(read('hiranuma'),e)).json()).cells.length,1);
  const error=payload({checked_at:stamp(1000),result:'error',cells:[]});
  await worker.fetch(ingest(error),e);
  const failed=await (await worker.fetch(read('hiranuma'),e)).json();
  assert.equal(failed.state,'unverified'); assert.deepEqual(failed.cells,[]);
  assert.equal((await e.DB.prepare('SELECT * FROM facility_snapshots WHERE facility = ?').bind('hiranuma').first()).payload,JSON.stringify(good.cells));
  assert.equal((await (await worker.fetch(ingest(good),e)).json()).updated,false);
  await worker.fetch(ingest(payload({checked_at:stamp(2000)})),e);
  assert.equal((await (await worker.fetch(read('hiranuma'),e)).json()).state,'source_checked');
});
test('failed first fetch and old successful snapshots never appear available',async()=>{
  const e=env();
  await worker.fetch(ingest(payload({result:'error',cells:[]})),e);
  assert.deepEqual((await (await worker.fetch(read('hiranuma'),e)).json()).cells,[]);
  await worker.fetch(ingest(payload({facility:'tobe',checked_at:stamp(-7*3600000)})),e);
  assert.equal((await (await worker.fetch(read('tobe'),e)).json()).state,'unverified');
});
test('reject unexpected fields, empty success, invalid dates and duplicate cells',()=>{
  assert.throws(()=>validate(payload({raw:'reservation names'})));
  assert.throws(()=>validate(payload({cells:[]})));
  const bad=payload(); bad.cells[0].date='2026-02-30'; assert.throws(()=>validate(bad));
  const duplicate=payload();duplicate.cells.push({...duplicate.cells[0]});assert.throws(()=>validate(duplicate));
});
test('image candidates cannot be promoted to confirmed availability',()=>{
  assert.throws(()=>validate(payload({facility:'katabira'})));
  const candidate=payload({facility:'katabira'}); candidate.cells[0].status='available_candidate';
  assert.equal(validate(candidate).cells[0].status,'available_candidate');
});
test('body limits, invalid facility and unavailable storage',async()=>{
  const e=env(); assert.equal((await worker.fetch(read('invalid'),e)).status,400);
  const r=new Request('https://example.test/ingest',{method:'POST',headers:{Authorization:'Bearer '+token},body:'x'.repeat(262145)});
  assert.equal((await worker.fetch(r,e)).status,400);
  const broken={...e,DB:{prepare(){throw new Error('private internals');}}};
  const response=await worker.fetch(read('hiranuma'),broken);
  assert.equal(response.status,503);assert.equal((await response.text()).includes('private internals'),false);
});
test('CORS permits only configured app and no data stays distinct from empty',async()=>{
  const e=env();const request=new Request(read('hiranuma'),{headers:{Origin:e.APP_ORIGIN}});
  const response=await worker.fetch(request,e);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'),e.APP_ORIGIN);
  assert.equal((await response.json()).state,'no_data');
  assert.equal((await worker.fetch(new Request(read('hiranuma'),{headers:{Origin:'https://other.test'}}),e)).headers.has('Access-Control-Allow-Origin'),false);
});
