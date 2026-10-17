// Draft service: public source availability only; never production/member data.
export const SCHEMA = `CREATE TABLE IF NOT EXISTS facility_snapshots (
  facility TEXT PRIMARY KEY,
  checked_at TEXT NOT NULL,
  success_at TEXT,
  result TEXT NOT NULL,
  payload TEXT
);`;
const FACILITIES = new Set(['hiranuma', 'tobe', 'karuizawa', 'katabira', 'kikuna']);
const STATUSES = new Set(['available_in_source', 'available_candidate', 'occupied',
  'unavailable', 'unknown', 'occupied_or_unknown']);
const MAX_BYTES = 256 * 1024;
const STALE_MS = 6 * 60 * 60 * 1000;
// Single-file Worker: can still be deployed using the Cloudflare dashboard.
// This is source-based time matching, not a booking or eligibility decision.
export const SEARCH_VERSION = 1;
const RULES_REVIEWED_AT = '2026-10-17';
const STANDARD_PERIODS = [['午前','09:00','13:00'],['午後','13:00','17:00'],['夜間','17:00','21:00']];
const FACILITY_RULES = {
  hiranuma: {
    name:'平沼集会所', rooms:['会議室A','会議室B','会議室C','和室'],
    source_url:'https://hiranuma.nkrsk.info/room/',
    rules_url:'https://hiranuma.nkrsk.info/use/',
    periods:STANDARD_PERIODS, sunday_holiday_no_night:true,
    monthly_monday:4, shift_holiday:false, year_end_start:'12-28', year_end_finish:'01-04',
    notes:['団体登録・予約受付条件の確認が必要です。',
      '連続2コマ利用は会議室C・和室について案内されています。他の部屋の連続利用は施設への確認が必要です。',
      '利用時間には片付け・清掃を含みます。']
  },
  tobe: {
    name:'戸部コミュニティハウス', rooms:['レクリエーション','集会室1','和室','集会室2'],
    source_url:'https://tobe.nkrsk.info/', rules_url:'https://tobe.nkrsk.info/use/',
    periods:STANDARD_PERIODS, sunday_holiday_no_night:true,
    monthly_monday:3, shift_holiday:true, year_end_start:'12-29', year_end_finish:'01-03',
    notes:['団体登録・予約受付条件の確認が必要です。',
      '原則1日1コマです。複数コマにまたがる利用は施設への確認が必要です。',
      '利用時間には片付け・清掃を含みます。']
  },
  karuizawa: {
    name:'軽井沢コミュニティハウス', rooms:['ホール','和室'],
    source_url:'https://karuizawa.nkrsk.info/', rules_url:'https://karuizawa.nkrsk.info/use/',
    periods:STANDARD_PERIODS, closed_weekdays:[1,4],
    year_end_start:'12-29', year_end_finish:'01-03', grouped_rooms:true,
    notes:['団体登録・予約受付条件の確認が必要です。',
      '予約表はホール・和室の単位です。ホールA/B、和室1/2の個別の空きと同じ部屋の継続利用は施設への確認が必要です。',
      '原則1日1時間帯です。複数時間帯の利用は施設への確認が必要です。',
      '利用時間には片付け・清掃を含みます。']
  },
  katabira: {
    name:'帷子小学校コミュニティハウス', rooms:['研修室I','研修室II','研修室III','和室'],
    source_url:'https://katabira.hodogaya-kumin.com/smartphone/yoyaku.html',
    rules_url:'https://katabira.hodogaya-kumin.com/smartphone/guide.html',
    hours_url:'https://katabira.hodogaya-kumin.com/smartphone/',
    periods:[['午前','09:00','12:00'],['午後','13:00','17:00'],['夜間','17:00','21:00']],
    closed_weekdays:[2,5], year_end_start:'12-29', year_end_finish:'01-03',
    notes:['画像の空欄を読み取った空室候補です。施設への確認が必要です。',
      '12〜13時は閉館です。団体登録・予約受付条件の確認が必要です。',
      '平日の登校日9〜15時は大きな音を出す利用に制限があります。学校の活動が優先されます。',
      '利用時間には片付け・清掃を含みます。']
  },
  kikuna: {
    name:'菊名コミュニティハウス', rooms:['A室','B室','S室（2F)'],
    source_url:'https://www.kouhoku-ksk.or.jp/kikunacomiha/smartphone/yoyaku/?type=on&roomlot=1572497566',
    rules_url:'https://www.kouhoku-ksk.or.jp/kikunacomiha/smartphone/riyou.html',
    periods:STANDARD_PERIODS, sunday_holiday_no_night:true,
    monthly_monday:3, shift_holiday:true, year_end_start:'12-29', year_end_finish:'01-03',
    notes:['団体登録・予約受付条件の確認が必要です。連続2時間帯は2コマとして扱われます。',
      '終了10分前から清掃・整理を行い、終了5分前までに終える必要があります。']
  }
};
// Cabinet Office published holidays, including substitute/citizens' holidays.
// Fail closed outside these reviewed years; do not guess future equinox dates.
const HOLIDAYS = {
  2026:new Set('01-01 01-12 02-11 02-23 03-20 04-29 05-03 05-04 05-05 05-06 07-20 08-11 09-21 09-22 09-23 10-12 11-03 11-23'.split(' ')),
  2027:new Set('01-01 01-11 02-11 02-23 03-21 03-22 04-29 05-03 05-04 05-05 07-19 08-11 09-20 09-23 10-11 11-03 11-23'.split(' '))
};
const HOLIDAY_SOURCE = 'https://www8.cao.go.jp/chosei/shukujitsu/gaiyou.html';

function isDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const d = new Date(value+'T00:00:00Z');
  return Number.isFinite(d.getTime()) && d.toISOString().slice(0,10) === value;
}
function minutes(value, allowMidnight = false) {
  if (allowMidnight && value === '24:00') return 1440;
  if (typeof value !== 'string' || !/^([01]\d|2[0-3]):[0-5]\d$/.test(value))
    throw new Error('Invalid time');
  return Number(value.slice(0,2))*60 + Number(value.slice(3));
}
export function parseSearch(params) {
  const allowed = ['date','start','end','facility'];
  for (const key of params.keys()) {
    if (!allowed.includes(key) || params.getAll(key).length !== 1) throw new Error('Invalid parameter');
  }
  const date=params.get('date'), start=params.get('start'), end=params.get('end');
  const facility=params.get('facility');
  if (!isDate(date) || (facility !== null && !FACILITIES.has(facility))) throw new Error('Invalid query');
  const startMinute=minutes(start), endMinute=minutes(end,true);
  if (startMinute >= endMinute) throw new Error('Invalid interval');
  return {date,start,end,startMinute,endMinute,facility,timezone:'Asia/Tokyo'};
}
function holiday(date) {return HOLIDAYS[Number(date.slice(0,4))]?.has(date.slice(5));}
export function dayPeriods(facility, date) {
  if (!FACILITIES.has(facility) || !isDate(date)) throw new Error('Invalid date or facility');
  const rule=FACILITY_RULES[facility], year=Number(date.slice(0,4)), md=date.slice(5);
  if (!HOLIDAYS[year]) return {reason:'calendar_unverified',periods:[]};
  const d=new Date(date+'T00:00:00Z'), weekday=d.getUTCDay();
  let closed=(md>=rule.year_end_start || md<=rule.year_end_finish) || rule.closed_weekdays?.includes(weekday);
  if (rule.monthly_monday) {
    const first=new Date(Date.UTC(year,d.getUTCMonth(),1));
    const day=1+(8-first.getUTCDay())%7+7*(rule.monthly_monday-1);
    let closure=new Date(Date.UTC(year,d.getUTCMonth(),day));
    if (rule.shift_holiday) {
      while (holiday(closure.toISOString().slice(0,10))) closure.setUTCDate(closure.getUTCDate()+1);
    }
    closed ||= date === closure.toISOString().slice(0,10);
  }
  if (closed) return {reason:'regular_closure',periods:[]};
  return {reason:null, periods:rule.periods.filter(([label])=>!(label==='夜間' &&
    rule.sunday_holiday_no_night && (weekday===0 || holiday(date))))
    .map(([period,start,end])=>({period,start,end}))};
}
function snapshotState(row, now) {
  if (!row) return 'no_data';
  const success=Date.parse(row.success_at), checked=Date.parse(row.checked_at);
  return row.result==='ok' && Number.isFinite(success) && Number.isFinite(checked) &&
    success===checked && now-success < STALE_MS && success-now<=5*60*1000 ? 'source_checked' : 'unverified';
}
function bookingChecks(facility, room, count) {
  const reasons=[];
  if (facility==='karuizawa') reasons.push('individual_room_unverified');
  if (count>1 && ['tobe','karuizawa'].includes(facility)) reasons.push('multi_period_use_requires_confirmation');
  if (facility==='hiranuma' && count>1 &&
      (!(room==='会議室C' || room==='和室') || count>2)) reasons.push('multi_period_use_requires_confirmation');
  return reasons;
}
export function searchFacility(facility, row, query, now=Date.now()) {
  const rule=FACILITY_RULES[facility];
  if (!rule) throw new Error('Invalid facility');
  const result={facility,name:rule.name,state:snapshotState(row,now),
    checked_at:row?.checked_at || null,success_at:row?.success_at || null,
    source_url:rule.source_url,rules_url:rule.rules_url,notes:rule.notes,
    booking_verified:false,matches:[],candidates:[],rooms:[],reason:null};
  if (result.state!=='source_checked') {result.reason=result.state;return result;}
  const schedule=dayPeriods(facility,query.date);
  if (schedule.reason) {result.reason=schedule.reason;return result;}
  const needed=schedule.periods.filter(p=>minutes(p.start)<query.endMinute && minutes(p.end)>query.startMinute);
  // Cover the whole requested interval, not merely an overlapping free period.
  let cursor=query.startMinute;
  for (const p of needed) {
    if (minutes(p.start)>cursor) break;
    cursor=Math.max(cursor,minutes(p.end));
  }
  if (cursor<query.endMinute) {result.reason='outside_open_periods';return result;}
  let cells;
  try {cells=JSON.parse(row.payload);} catch {result.state='unverified';result.reason='invalid_snapshot';return result;}
  if (!Array.isArray(cells) || cells.length>1500) {result.state='unverified';result.reason='invalid_snapshot';return result;}
  const index=new Map();
  for (const cell of cells) {
    if (!cell || typeof cell!=='object' || cell.date!==query.date) continue;
    const key=JSON.stringify([cell.room,cell.period]);
    if (index.has(key)) {result.state='unverified';result.reason='duplicate_source_cell';return result;}
    index.set(key,cell);
  }
  for (const room of rule.rooms) {
    const selected=needed.map(p=>index.get(JSON.stringify([room,p.period])));
    let reason=null;
    if (selected.some(c=>!c)) reason='missing_source_cell';
    else if (selected.some(c=>!STATUSES.has(c.status) || c.status==='unknown')) reason='source_unknown';
    else if (selected.some(c=>!['available_in_source','available_candidate'].includes(c.status))) reason='source_not_available';
    if (reason) {result.rooms.push({room,reason});continue;}
    const confirmation=bookingChecks(facility,room,needed.length);
    // Katabira is always a candidate, even if storage were to contain an invalid promoted status.
    if (facility==='katabira' || selected.some(c=>c.status==='available_candidate')) confirmation.push('image_candidate');
    const candidate={room,periods:needed.map(p=>({...p})),
      room_scope:rule.grouped_rooms?'source_group':'individual_room',
      same_individual_room_verified:!rule.grouped_rooms,
      booking_verified:false,confirmation_reasons:confirmation};
    if (confirmation.length) result.candidates.push(candidate); else result.matches.push(candidate);
    result.rooms.push({room,reason:confirmation.length?'needs_confirmation':'source_time_match'});
  }
  result.reason=result.matches.length?'source_time_matches':result.candidates.length?'candidate_only':
    result.rooms.some(r=>['missing_source_cell','source_unknown'].includes(r.reason))?'insufficient_source_data':'no_source_time_match';
  return result;
}
function catalog() {
  return {search_version:SEARCH_VERSION,timezone:'Asia/Tokyo',rules_reviewed_at:RULES_REVIEWED_AT,
    holiday_years:Object.keys(HOLIDAYS).map(Number),holiday_source:HOLIDAY_SOURCE,
    facilities:[...FACILITIES].map(facility=>({facility,...FACILITY_RULES[facility]}))};
}
export const UPSERT = `INSERT INTO facility_snapshots
  (facility, checked_at, success_at, result, payload) VALUES (?, ?, ?, ?, ?)
  ON CONFLICT(facility) DO UPDATE SET checked_at=excluded.checked_at,
  result=excluded.result,
  success_at=COALESCE(excluded.success_at,facility_snapshots.success_at),
  payload=COALESCE(excluded.payload,facility_snapshots.payload)
  WHERE excluded.checked_at > facility_snapshots.checked_at`;

function json(data, status = 200, origin = null) {
  const headers = {'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'};
  if (origin) headers['Access-Control-Allow-Origin'] = origin;
  return new Response(JSON.stringify(data), {status, headers});
}
function onlyKeys(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).some(k => !keys.includes(k))) throw new Error('Unexpected fields');
}
export function validate(input, now = Date.now()) {
  onlyKeys(input, ['schema_version', 'facility', 'checked_at', 'result', 'cells']);
  if (input.schema_version !== 1 || !FACILITIES.has(input.facility) ||
      !['ok','error'].includes(input.result)) throw new Error('Invalid envelope');
  const when = Date.parse(input.checked_at);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(input.checked_at) ||
      !Number.isFinite(when) || now-when > 24*60*60*1000 || when-now > 5*60*1000)
    throw new Error('Invalid check time');
  if (!Array.isArray(input.cells) || input.cells.length > 1500 ||
      (input.result === 'ok' && input.cells.length === 0) ||
      (input.result === 'error' && input.cells.length !== 0)) throw new Error('Invalid cell count');
  const seen = new Set();
  for (const cell of input.cells) {
    onlyKeys(cell, ['room', 'date', 'period', 'status']);
    if (typeof cell.room !== 'string' || !cell.room.trim() || cell.room.length > 80 ||
        !/^\d{4}-\d{2}-\d{2}$/.test(cell.date) ||
        !['午前','午後','夜間'].includes(cell.period) || !STATUSES.has(cell.status))
      throw new Error('Invalid cell');
    const date = new Date(cell.date+'T00:00:00Z');
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0,10) !== cell.date)
      throw new Error('Invalid calendar date');
    // Image blanks must never be promoted to confirmed availability.
    if (input.facility === 'katabira' && cell.status === 'available_in_source')
      throw new Error('Image availability needs review');
    const key = JSON.stringify([cell.room,cell.date,cell.period]);
    if (seen.has(key)) throw new Error('Duplicate cell');
    seen.add(key);
  }
  return input;
}
async function readLimited(request) {
  if (Number(request.headers.get('Content-Length')) > MAX_BYTES) throw new Error('Body too large');
  const reader = request.body?.getReader();
  if (!reader) throw new Error('Missing body');
  let total = 0; const parts = [];
  for (;;) {
    const {done,value} = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BYTES) {await reader.cancel(); throw new Error('Body too large');}
    parts.push(value);
  }
  const bytes = new Uint8Array(total); let offset = 0;
  for (const part of parts) {bytes.set(part,offset); offset += part.byteLength;}
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get('Origin') === env.APP_ORIGIN ? env.APP_ORIGIN : null;
    if (url.pathname === '/health' && request.method === 'GET')
      return json({service:'rehearsal-availability',schema_version:1,stage:'draft',search_version:SEARCH_VERSION},200,origin);
    if (url.pathname === '/facilities' && request.method === 'GET') return json(catalog(),200,origin);
    if (!env.DB) return json({error:'service_not_configured'},503,origin);
    if (url.pathname === '/ingest' && request.method === 'POST') {
      // No fallback token, no token in URLs, no logging of request bodies.
      if (typeof env.INGEST_TOKEN !== 'string' || env.INGEST_TOKEN.length < 32)
        return json({error:'ingest_not_configured'},503);
      if (request.headers.get('Authorization') !== 'Bearer '+env.INGEST_TOKEN)
        return json({error:'unauthorized'},401);
      let input;
      try {input = validate(await readLimited(request));}
      catch {return json({error:'invalid_payload'},400);}
      try {
        const success = input.result === 'ok';
        const result = await env.DB.prepare(UPSERT).bind(input.facility, input.checked_at,
          success ? input.checked_at : null, input.result,
          success ? JSON.stringify(input.cells) : null).run();
        return json({accepted:true,updated:result.meta.changes > 0});
      } catch {return json({error:'storage_unavailable'},503);}
    }
    if (url.pathname === '/availability' && request.method === 'GET') {
      const facility = url.searchParams.get('facility');
      if (!FACILITIES.has(facility)) return json({error:'invalid_facility'},400,origin);
      try {
        const row = await env.DB.prepare('SELECT * FROM facility_snapshots WHERE facility = ?')
          .bind(facility).first();
        if (!row) return json({facility,state:'no_data',cells:[]},200,origin);
        const stale = !row.success_at || Date.now()-Date.parse(row.success_at) > STALE_MS;
        const degraded = row.result !== 'ok' || stale;
        return json({schema_version:1,facility,checked_at:row.checked_at,success_at:row.success_at,
          state:degraded ? 'unverified' : 'source_checked',
          booking_verified:false,time_ranges_normalized:false,
          // Keep last good data in storage, but do not present it as current availability.
          cells:degraded ? [] : JSON.parse(row.payload || '[]')},200,origin);
      } catch {return json({error:'storage_unavailable'},503,origin);}
    }
    if (url.pathname === '/search' && request.method === 'GET') {
      let query;
      try {query=parseSearch(url.searchParams);}
      catch {return json({error:'invalid_search',format:'date=YYYY-MM-DD&start=HH:mm&end=HH:mm',
        timezone:'Asia/Tokyo',same_day_only:true},400,origin);}
      try {
        const ids=query.facility?[query.facility]:[...FACILITIES];
        const rows=await Promise.all(ids.map(id=>env.DB.prepare('SELECT * FROM facility_snapshots WHERE facility = ?').bind(id).first()));
        const now=Date.now();
        const results=ids.map((id,i)=>searchFacility(id,rows[i],query,now));
        return json({search_version:SEARCH_VERSION,query:{date:query.date,start:query.start,end:query.end,timezone:query.timezone},
          rules_reviewed_at:RULES_REVIEWED_AT,booking_verified:false,
          notice:'公開予約表と利用時間に基づく候補です。予約の確約・団体の利用資格・残りの予約回数・稽古内容への適合性は確認していません。',
          results},200,origin);
      } catch {return json({error:'storage_unavailable'},503,origin);}
    }
    return json({error:'not_found'},404,origin);
  }
};
