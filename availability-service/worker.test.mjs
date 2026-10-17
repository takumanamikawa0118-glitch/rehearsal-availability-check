import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import worker, {SCHEMA,validate,parseSearch,dayPeriods,searchFacility} from './worker.mjs';

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

// Fixed clock avoids timezone and wall-clock dependent search assertions.
const NOW=Date.parse('2026-10-17T06:00:00Z');
const query=(date='2026-10-24',start='13:00',end='17:00',facility=null)=>parseSearch(
  new URLSearchParams({date,start,end,...(facility?{facility}:{})}));
const cell=(room,period,status='available_in_source',date='2026-10-24')=>({room,period,status,date});
function row(cells,changes={}) {
  return {checked_at:'2026-10-17T06:00:00Z',success_at:'2026-10-17T06:00:00Z',
    result:'ok',payload:JSON.stringify(cells),...changes};
}
const search=(id,cells,q=query(),changes={})=>searchFacility(id,row(cells,changes),q,NOW);

test('five verified time mappings; Katabira lunch is a real gap',()=>{
  for (const id of ['hiranuma','tobe','karuizawa','kikuna']) {
    assert.deepEqual(dayPeriods(id,'2026-10-24').periods.map(p=>[p.start,p.end]),
      [['09:00','13:00'],['13:00','17:00'],['17:00','21:00']]);
  }
  assert.deepEqual(dayPeriods('katabira','2026-10-24').periods.map(p=>[p.start,p.end]),
    [['09:00','12:00'],['13:00','17:00'],['17:00','21:00']]);
});
test('invalid, duplicate, unknown and overnight query parameters are rejected',()=>{
  for (const changes of [
    {date:'2026-02-30'},{date:'2027-02-29'},{date:'2026-1-1'},
    {start:'9:00'},{start:'24:00'},{start:'13:60'},{end:'25:00'},
    {start:'17:00',end:'17:00'},{start:'20:00',end:'09:00'},
    {facility:'other'},{facility:''},{timezone:'UTC'},
  ]) assert.throws(()=>parseSearch(new URLSearchParams({date:'2026-10-24',start:'13:00',end:'17:00',...changes})));
  assert.throws(()=>parseSearch(new URLSearchParams('date=2026-10-24&start=13:00&end=17:00&end=21:00')));
  assert.throws(()=>parseSearch(new URLSearchParams('date=2026-10-24&start=13:00')));
  assert.equal(query('2026-10-24','23:00','24:00').endMinute,1440);
});
test('end boundary is exclusive; start boundary selects the following period',()=>{
  const cells=[cell('会議室C','午前'),cell('会議室C','午後','occupied'),cell('会議室C','夜間')];
  assert.equal(search('hiranuma',cells,query('2026-10-24','09:00','13:00')).matches.length,1);
  assert.equal(search('hiranuma',cells,query('2026-10-24','13:00','17:00')).matches.length,0);
  assert.equal(search('hiranuma',cells,query('2026-10-24','17:00','21:00')).matches.length,1);
  assert.equal(search('hiranuma',cells,query('2026-10-24','12:59','13:01')).matches.length,0);
});
test('same room must cover every period; no switching between free rooms',()=>{
  const cells=[cell('会議室C','午後'),cell('和室','夜間')];
  const partial=search('hiranuma',cells,query('2026-10-24','16:30','20:00'));
  assert.equal(partial.matches.length,0);assert.equal(partial.candidates.length,0);
  cells.push(cell('会議室C','夜間'));
  const covered=search('hiranuma',cells,query('2026-10-24','16:30','20:00'));
  assert.deepEqual(covered.matches.map(m=>m.room),['会議室C']);
  assert.deepEqual(covered.matches[0].periods.map(p=>p.period),['午後','夜間']);
});
test('out of hours, closing time overrun and lunch gaps never match',()=>{
  const cs=['午前','午後','夜間'].map(p=>cell('研修室I',p,'available_candidate'));
  for (const [start,end] of [['08:59','09:30'],['11:00','13:30'],['12:00','13:00'],['20:00','21:01'],['00:00','24:00']]) {
    const r=search('katabira',cs,query('2026-10-24',start,end));
    assert.equal(r.reason,'outside_open_periods');assert.equal(r.candidates.length,0);
  }
  assert.equal(search('katabira',cs,query('2026-10-24','09:00','12:00')).candidates.length,1);
  assert.equal(search('katabira',cs,query('2026-10-24','13:00','21:00')).candidates.length,1);
});
test('Sunday and holiday night restrictions include substitute and citizens holidays',()=>{
  for (const id of ['hiranuma','tobe','kikuna']) {
    for (const date of ['2026-10-18','2026-11-03','2026-05-06','2026-09-22','2027-03-22']) {
      assert.equal(dayPeriods(id,date).periods.some(p=>p.period==='夜間'),false);
    }
  }
  assert.equal(dayPeriods('karuizawa','2026-10-18').periods.length,3);
  assert.equal(dayPeriods('katabira','2026-10-18').periods.length,3);
});
test('weekly, monthly and shifted holiday closure rules',()=>{
  assert.equal(dayPeriods('hiranuma','2026-10-26').reason,'regular_closure');
  for (const id of ['tobe','kikuna']) {
    assert.equal(dayPeriods(id,'2026-10-19').reason,'regular_closure');
    // 2026-09-21, 22, 23 are holidays: third-Monday closure moves to Thursday.
    assert.equal(dayPeriods(id,'2026-09-24').reason,'regular_closure');
    assert.equal(dayPeriods(id,'2026-09-21').reason,null);
  }
  assert.equal(dayPeriods('karuizawa','2026-10-19').reason,'regular_closure');
  assert.equal(dayPeriods('karuizawa','2026-10-22').reason,'regular_closure');
  assert.equal(dayPeriods('katabira','2026-10-20').reason,'regular_closure');
  assert.equal(dayPeriods('katabira','2026-10-23').reason,'regular_closure');
  const closed=search('tobe',[cell('和室','午後','available_in_source','2026-10-19')],query('2026-10-19'));
  assert.equal(closed.reason,'regular_closure');assert.equal(closed.matches.length,0);
});
test('year-end closures and unreviewed calendar years fail closed',()=>{
  for (const id of ['hiranuma','tobe','karuizawa','katabira','kikuna']) {
    assert.equal(dayPeriods(id,'2026-12-29').reason,'regular_closure');
    assert.equal(dayPeriods(id,'2027-01-03').reason,'regular_closure');
    assert.equal(dayPeriods(id,'2028-02-29').reason,'calendar_unverified');
  }
  assert.equal(dayPeriods('hiranuma','2027-01-04').reason,'regular_closure');
  assert.equal(dayPeriods('tobe','2027-01-04').reason,null);
});
test('stale, failed, missing, invalid or future snapshots never match',()=>{
  const cells=[cell('会議室C','午後')];
  for (const changes of [
    {result:'error'},{success_at:null},{success_at:'invalid'},
    {success_at:'2026-10-17T00:00:00Z',checked_at:'2026-10-17T00:00:00Z'},
    {success_at:'2026-10-17T06:06:00Z',checked_at:'2026-10-17T06:06:00Z'},
    {checked_at:'2026-10-17T06:01:00Z'},
  ]) assert.equal(search('hiranuma',cells,query(),changes).state,'unverified');
  assert.equal(searchFacility('hiranuma',null,query(),NOW).state,'no_data');
  assert.equal(search('hiranuma',cells,query(),{payload:'broken'}).reason,'invalid_snapshot');
  assert.equal(search('hiranuma',cells,query(),{payload:'{}'}).reason,'invalid_snapshot');
  assert.equal(search('hiranuma',[...cells,...cells]).reason,'duplicate_source_cell');
});
test('missing/unknown dates and reservation states do not become free',()=>{
  for (const status of ['occupied','unavailable','unknown','occupied_or_unknown','unexpected']) {
    const r=search('hiranuma',[cell('会議室C','午後',status)]);
    assert.equal(r.matches.length,0);assert.equal(r.candidates.length,0);
  }
  assert.equal(search('hiranuma',[cell('会議室C','午後')],query('2026-11-24')).reason,'insufficient_source_data');
  assert.equal(search('hiranuma',[cell('new room','午後')]).matches.length,0);
});
test('image and room-group candidates are kept separate from source time matches',()=>{
  const image=search('katabira',[cell('研修室I','午後','available_candidate')]);
  assert.equal(image.matches.length,0);assert.deepEqual(image.candidates[0].confirmation_reasons,['image_candidate']);
  const badPromoted=search('katabira',[cell('研修室I','午後')]);
  assert.equal(badPromoted.matches.length,0);
  const group=search('karuizawa',[cell('ホール','午後')]);
  assert.equal(group.matches.length,0);assert.equal(group.candidates[0].same_individual_room_verified,false);
  assert.equal(group.candidates[0].room_scope,'source_group');
});
test('multiple-period eligibility is not assumed from empty cells',()=>{
  for (const [id,room] of [['tobe','和室'],['karuizawa','ホール'],['hiranuma','会議室A']]) {
    const r=search(id,[cell(room,'午後'),cell(room,'夜間')],query('2026-10-24','16:00','18:00'));
    assert.equal(r.matches.length,0);
    assert.ok(r.candidates[0].confirmation_reasons.includes('multi_period_use_requires_confirmation'));
  }
  const c=search('kikuna',[cell('A室','午後'),cell('A室','夜間')],query('2026-10-24','16:00','18:00'));
  assert.equal(c.matches.length,1);assert.equal(c.matches[0].booking_verified,false);
});
test('search API uses real stored data, reads no secrets and preserves old readback format',async()=>{
  const e=env();const data=payload({cells:[cell('会議室C','午後')]});
  await worker.fetch(ingest(data),e);
  const response=await worker.fetch(new Request('https://example.test/search?date=2026-10-24&start=13:00&end=17:00',
    {headers:{Origin:e.APP_ORIGIN}}),e);
  assert.equal(response.status,200);assert.equal(response.headers.get('Access-Control-Allow-Origin'),e.APP_ORIGIN);
  const answer=await response.json();
  assert.equal(answer.results.length,5);assert.equal(answer.results[0].matches.length,1);
  assert.equal(answer.results[1].state,'no_data');assert.equal(answer.booking_verified,false);
  assert.equal(JSON.stringify(answer).includes(token),false);
  assert.equal(answer.query.timezone,'Asia/Tokyo');
  const original=await (await worker.fetch(read('hiranuma'),e)).json();
  assert.deepEqual(original.cells,data.cells);assert.equal(original.time_ranges_normalized,false);
  const filtered=await (await worker.fetch(new Request('https://example.test/search?date=2026-10-24&start=13:00&end=17:00&facility=hiranuma'),e)).json();
  assert.equal(filtered.results.length,1);
});
test('search input failures, database failures and catalog metadata',async()=>{
  const e=env();
  assert.equal((await worker.fetch(new Request('https://example.test/search?date=2026-10-24'),e)).status,400);
  const broken={...e,DB:{prepare(){throw new Error('private DB error');}}};
  const r=await worker.fetch(new Request('https://example.test/search?date=2026-10-24&start=13:00&end=17:00'),broken);
  assert.equal(r.status,503);assert.equal((await r.text()).includes('private DB'),false);
  const catalog=await (await worker.fetch(new Request('https://example.test/facilities'),{})).json();
  assert.equal(catalog.facilities.length,5);assert.deepEqual(catalog.holiday_years,[2026,2027]);
  assert.equal(catalog.rules_reviewed_at,'2026-10-17');
  const health=await (await worker.fetch(new Request('https://example.test/health'),{})).json();
  assert.equal(health.search_version,1);
});
